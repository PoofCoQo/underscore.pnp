"""Phase-6 orchestration over the existing realtime AudioEngine."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable

from pnp_music_player.audio.cache import AudioCache
from pnp_music_player.audio.engine import AudioEngine, PlaybackSnapshot
from pnp_music_player.audio.loader import (
    DecodedAudio,
    estimate_decoded_bytes,
    load_audio,
)
from pnp_music_player.audio.output_tap import DiscordPcmTap
from pnp_music_player.audio.transitions import TransitionMode
from pnp_music_player.models.track import TrackMetadata
from pnp_music_player.ui.persistence import MetadataReadError, MetadataStore
from pnp_music_player.ui.metadata_paths import is_foreign_absolute_path
from pnp_music_player.ui.player_settings import PlayerSettings, PlayerSettingsStore

ALL_CATEGORY = "All"
FAVORITES_CATEGORY = "Favorites"
NO_CATEGORY = "No Category"
ALL_ALBUMS = "All Albums"
NO_ALBUM = "No Album"


@dataclass(slots=True)
class LibraryTrack:
    key: str
    metadata: TrackMetadata
    valid: bool
    error: str = ""
    audio: DecodedAudio | None = None


def track_key(metadata: TrackMetadata) -> str:
    digest = hashlib.sha256(metadata.filepath.encode("utf-8")).hexdigest()[:16]
    return f"{metadata.id}:{digest}"


def load_library(
    store: MetadataStore, *, problems: list[MetadataReadError] | None = None
) -> list[LibraryTrack]:
    tracks: list[LibraryTrack] = []
    metadata_items, found_problems = store.scan_all()
    if problems is not None:
        problems.extend(found_problems)
    for metadata in metadata_items:
        valid, error = True, ""
        try:
            if is_foreign_absolute_path(metadata.filepath) or not Path(metadata.filepath).is_file():
                raise FileNotFoundError("Audio file is missing")
            highest_beat = max(
                metadata.beat_positions + metadata.detected_beat_positions,
                default=0,
            )
            metadata.validate(max(metadata.play_end, highest_beat + 1))
        except Exception as exc:
            valid, error = False, str(exc)
        tracks.append(LibraryTrack(track_key(metadata), metadata, valid, error))
    return sorted(tracks, key=lambda item: (item.metadata.title.casefold(), item.key))


class PlayerController:
    """Queue/transition controller with no Qt and no playback implementation."""

    def __init__(
        self,
        engine: AudioEngine,
        tracks: list[LibraryTrack],
        settings_store: PlayerSettingsStore,
        *,
        audio_loader: Callable[[str | Path], DecodedAudio] = load_audio,
        audio_cache: AudioCache | None = None,
        discord_output: DiscordPcmTap | None = None,
    ) -> None:
        self.engine = engine
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.audio_loader = audio_loader
        self.audio_cache = audio_cache or AudioCache()
        self.discord_output = discord_output
        self.tracks: dict[str, LibraryTrack] = {track.key: track for track in tracks}
        for track in self.tracks.values():
            if track.audio is not None:
                self.audio_cache.put(track.metadata.filepath, track.audio)
                track.audio = None
        self.current_category = self.settings.last_category
        self.current_album = self.settings.last_album
        self.transition_mode = TransitionMode(self.settings.transition_mode)
        self.transition_duration = self.settings.transition_duration
        self.master_db = self.settings.master_db
        self.muted = False  # Mute deliberately does not persist across restarts.
        self.discord_db = self.settings.discord_db
        self.discord_muted = False
        self._pending_current: str | None = None
        self._pending_queue: str | None = None
        self._pending_queue_metadata: TrackMetadata | None = None
        self._pending_queue_entry_id: str | None = None
        self._pending_queue_cancel: str | None = None
        self._pending_transition: str | None = None
        self._pending_paused: bool | None = None
        self.engine.set_master_volume_db(self.master_db)
        self.engine.set_muted(False)
        if self.discord_output is not None:
            self.discord_output.set_volume_db(self.discord_db)
            self.discord_output.set_muted(False)

    @property
    def categories(self) -> list[str]:
        values = sorted(
            {
                track.metadata.category.strip() or NO_CATEGORY
                for track in self.tracks.values()
            }
            - {ALL_CATEGORY, FAVORITES_CATEGORY},
            key=str.casefold,
        )
        return [ALL_CATEGORY, FAVORITES_CATEGORY, *values]

    @property
    def albums(self) -> list[str]:
        values = sorted(
            {
                track.metadata.album.strip() or NO_ALBUM
                for track in self.tracks.values()
            }
            - {ALL_ALBUMS},
            key=str.casefold,
        )
        return [ALL_ALBUMS, *values]

    def replace_library(self, tracks: list[LibraryTrack]) -> None:
        self.tracks = {track.key: track for track in tracks}
        for track in self.tracks.values():
            if track.audio is not None:
                self.audio_cache.put(
                    track.metadata.filepath,
                    track.audio,
                    protected_paths=self.protected_audio_paths(),
                )
                track.audio = None
        if self.current_category not in self.categories:
            self.set_category(ALL_CATEGORY)
        if self.current_album not in self.albums:
            self.set_album(ALL_ALBUMS)

    def set_category(self, category: str) -> None:
        self.current_category = category if category in self.categories else ALL_CATEGORY
        self.settings.last_category = self.current_category
        self._save_settings()

    def set_album(self, album: str) -> None:
        self.current_album = album if album in self.albums else ALL_ALBUMS
        self.settings.last_album = self.current_album
        self._save_settings()

    def toggle_favorite(self, key: str) -> bool:
        track = self._track(key)
        track.metadata.favorite = not track.metadata.favorite
        return track.metadata.favorite

    def favorite_key_at(self, position: int) -> str | None:
        """Return a one-based favorite in the stable player display order."""
        if not 1 <= position <= 10:
            return None
        favorites = [
            track.key
            for track in self.tracks.values()
            if track.metadata.favorite
        ]
        index = position - 1
        return favorites[index] if index < len(favorites) else None

    def prepare(self, key: str) -> LibraryTrack:
        track = self._track(key)
        if not track.valid:
            raise ValueError(track.error or "Track requires setup")
        audio = self.audio_cache.get(track.metadata.filepath)
        if audio is None:
            audio = self.audio_loader(track.metadata.filepath)
            self.store_audio(key, audio)
        track.metadata.validate(audio.total_frames)
        return track

    def has_cached_audio(self, key: str) -> bool:
        track = self._track(key)
        return self.audio_cache.contains(track.metadata.filepath)

    def store_audio(self, key: str, audio: DecodedAudio) -> None:
        track = self._track(key)
        track.metadata.validate(audio.total_frames)
        self.audio_cache.put(
            track.metadata.filepath,
            audio,
            protected_paths=(*self.protected_audio_paths(), track.metadata.filepath),
        )
        track.audio = None

    def make_room_for_load(self, key: str) -> None:
        track = self._track(key)
        expected = estimate_decoded_bytes(track.metadata.filepath)
        # load_audio temporarily holds source PCM and converted PCM together.
        self.audio_cache.make_room(
            expected * 2,
            protected_paths=self.protected_audio_paths(),
        )

    def select_track(self, key: str) -> None:
        self.select_prepared(self.prepare(key))

    def select_prepared(self, track: LibraryTrack) -> None:
        audio = self._audio_for(track)
        snapshot = self.engine.snapshot()
        self.synchronize(snapshot, trim_cache=False)
        if not snapshot.playing and self._pending_current == track.key:
            return
        if snapshot.playing or self._pending_current is not None:
            if self.queued_key(snapshot) == track.key:
                self.cancel_queued_if_looping(snapshot)
                return
            self.engine.queue_track(audio, track.metadata)
            self._pending_queue = track.key
            self._pending_queue_metadata = TrackMetadata.from_dict(
                track.metadata.to_dict()
            )
            self._pending_queue_entry_id = "intro"
            self._pending_queue_cancel = None
            default_mode = _transition_mode(track.metadata.default_transition)
            self.transition_mode = default_mode
            self.transition_duration = min(
                10.0, max(0.5, track.metadata.default_fade_duration)
            )
        else:
            self.engine.play(audio, track.metadata)
            self._pending_current = track.key
            self._pending_queue = None
            self._pending_queue_metadata = None
            self._pending_queue_entry_id = None
            self._pending_queue_cancel = None
        self.trim_audio_cache(snapshot)

    def cancel_queued_if_looping(
        self, snapshot: PlaybackSnapshot | None = None
    ) -> bool:
        snapshot = snapshot or self.engine.snapshot()
        key = self.queued_key(snapshot)
        if key is None:
            return False
        queued_is_pending = snapshot.queued_track_id is None and self._pending_queue == key
        if not (snapshot.can_cancel_queued_track or queued_is_pending):
            return False
        self.engine.cancel_queued_track()
        self._pending_queue = None
        self._pending_queue_metadata = None
        self._pending_queue_entry_id = None
        self._pending_queue_cancel = key
        return True

    def can_cancel_queue(self, snapshot: PlaybackSnapshot | None = None) -> bool:
        snapshot = snapshot or self.engine.snapshot()
        key = self.queued_key(snapshot)
        if key is None:
            return False
        if self._pending_queue == key:
            return True
        snapshot_key = self._key_for_track(
            snapshot.queued_track_id, snapshot.queued_track_filepath
        )
        return snapshot.can_cancel_queued_track and snapshot_key == key

    def set_transition_mode(self, mode: TransitionMode | str) -> None:
        self.transition_mode = _transition_mode(mode)
        self.settings.transition_mode = self.transition_mode.value
        self._save_settings()

    def set_transition_duration(self, seconds: float) -> None:
        self.transition_duration = min(10.0, max(0.5, float(seconds)))
        self.settings.transition_duration = self.transition_duration
        self._save_settings()

    def force_transition(self) -> None:
        if self.transition_mode is TransitionMode.NORMAL:
            return
        snapshot = self.engine.snapshot()
        self.synchronize(snapshot, trim_cache=False)
        if self._pending_transition is not None:
            return
        key = self.queued_key(snapshot)
        if key is None:
            return
        track = self.prepare(key)
        audio = self._audio_for(track)
        playback_metadata = self._queued_playback_metadata(snapshot, key)
        entry_id = self.queued_entry_id(snapshot) or "intro"
        self.engine.transition_to(
            audio,
            playback_metadata,
            mode=self.transition_mode,
            duration_seconds=(
                0.0 if self.transition_mode is TransitionMode.HARD_CUT
                else self.transition_duration
            ),
            entry_id=entry_id,
        )
        self._pending_current = key
        self._pending_queue = None
        self._pending_queue_metadata = None
        self._pending_queue_entry_id = None
        self._pending_queue_cancel = None
        self._pending_transition = key
        self.trim_audio_cache(snapshot)

    def pause_resume(self) -> None:
        self.synchronize(self.engine.snapshot())
        desired = not self.effective_paused()
        if desired:
            self.engine.pause()
        else:
            self.engine.resume()
        self._pending_paused = desired

    def effective_paused(self, snapshot: PlaybackSnapshot | None = None) -> bool:
        if self._pending_paused is not None:
            return self._pending_paused
        return (snapshot or self.engine.snapshot()).paused

    def transition_in_flight(
        self, snapshot: PlaybackSnapshot | None = None
    ) -> bool:
        snapshot = snapshot or self.engine.snapshot()
        return (
            self._pending_transition is not None
            or snapshot.transition_target_id is not None
        )

    def select_loop(self, loop_id: str) -> None:
        snapshot = self.engine.snapshot()
        self.synchronize(snapshot)
        if self.transition_in_flight(snapshot):
            raise ValueError("Cannot change loops during a transition")
        playback_metadata = self.engine.current_track
        if playback_metadata is None or not snapshot.playing:
            raise ValueError("No track is active")
        # The callback owns an immutable copy of the metadata with which this
        # playback began. A library refresh may already contain newer loops;
        # they only become available the next time the track is started.
        playback_metadata.loop_bounds(loop_id)
        self.engine.select_loop(loop_id)

    def select_queued_entry(self, entry_id: str) -> bool:
        snapshot = self.engine.snapshot()
        self.synchronize(snapshot)
        if self.transition_in_flight(snapshot):
            raise ValueError("Cannot change the start section during a transition")
        key = self.queued_key(snapshot)
        if key is None:
            raise ValueError("No NEXT track selected")
        metadata = self._queued_playback_metadata(snapshot, key)
        _validate_entry_id(metadata, entry_id)
        if self.queued_entry_id(snapshot) == entry_id:
            return False
        self.engine.select_queued_entry(entry_id)
        self._pending_queue_entry_id = entry_id
        return True

    def stop(self) -> None:
        self.engine.stop()
        self._pending_current = None
        self._pending_queue = None
        self._pending_queue_metadata = None
        self._pending_queue_entry_id = None
        self._pending_queue_cancel = None
        self._pending_transition = None
        self._pending_paused = None

    def set_master_db(self, value: float, *, persist: bool = True) -> None:
        self.master_db = min(0.0, max(-80.0, float(value)))
        self.engine.set_master_volume_db(self.master_db)
        self.settings.master_db = self.master_db
        if persist:
            self._save_settings()

    def persist_settings(self) -> None:
        self._save_settings()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self.engine.set_muted(self.muted)

    def set_discord_db(self, value: float, *, persist: bool = True) -> None:
        self.discord_db = min(0.0, max(-80.0, float(value)))
        if self.discord_output is not None:
            self.discord_output.set_volume_db(self.discord_db)
        self.settings.discord_db = self.discord_db
        if persist:
            self._save_settings()

    def set_discord_muted(self, muted: bool) -> None:
        self.discord_muted = bool(muted)
        if self.discord_output is not None:
            self.discord_output.set_muted(self.discord_muted)

    def current_key(self, snapshot: PlaybackSnapshot | None = None) -> str | None:
        snapshot = snapshot or self.engine.snapshot()
        return (
            self._key_for_track(snapshot.track_id, snapshot.track_filepath)
            or self._pending_current
        )

    def queued_key(self, snapshot: PlaybackSnapshot | None = None) -> str | None:
        snapshot = snapshot or self.engine.snapshot()
        snapshot_key = self._key_for_track(
            snapshot.queued_track_id, snapshot.queued_track_filepath
        )
        if self._pending_queue_cancel is not None:
            return None
        if self._pending_queue is not None:
            return self._pending_queue
        if self._pending_transition is not None:
            return None
        return snapshot_key

    def queued_entry_id(
        self, snapshot: PlaybackSnapshot | None = None
    ) -> str | None:
        snapshot = snapshot or self.engine.snapshot()
        if self.queued_key(snapshot) is None:
            return None
        if self._pending_queue_entry_id is not None:
            return self._pending_queue_entry_id
        return snapshot.queued_entry_id or "intro"

    def queued_parts(
        self, snapshot: PlaybackSnapshot | None = None
    ) -> list[dict[str, object]]:
        snapshot = snapshot or self.engine.snapshot()
        key = self.queued_key(snapshot)
        if key is None:
            return []
        metadata = self._queued_playback_metadata(snapshot, key)
        selected = self.queued_entry_id(snapshot)
        parts: list[tuple[str, str]] = [
            ("intro", "Intro"),
            ("loop:main", metadata.main_loop_name),
            *[
                (f"loop:{region.id}", region.name)
                for region in metadata.alternate_loops
            ],
            ("outro", "Outro"),
        ]
        return [
            {"id": entry_id, "name": name, "selected": entry_id == selected}
            for entry_id, name in parts
        ]

    def synchronize(
        self, snapshot: PlaybackSnapshot, *, trim_cache: bool = True
    ) -> None:
        current_key = self._key_for_track(
            snapshot.track_id, snapshot.track_filepath
        )
        queued_key = self._key_for_track(
            snapshot.queued_track_id, snapshot.queued_track_filepath
        )
        transition_key = self._key_for_track(
            snapshot.transition_target_id, snapshot.transition_target_filepath
        )
        if self._pending_transition is not None and (
            transition_key == self._pending_transition
            or (
                current_key == self._pending_transition
                and queued_key != self._pending_transition
            )
        ):
            self._pending_transition = None
        if (
            self._pending_current is not None
            and current_key == self._pending_current
            and self._pending_transition is None
        ):
            self._pending_current = None
        if self._pending_queue is not None and queued_key == self._pending_queue:
            self._pending_queue = None
            self._pending_queue_metadata = None
        if (
            self._pending_queue_entry_id is not None
            and self._pending_queue is None
            and snapshot.queued_entry_id == self._pending_queue_entry_id
        ):
            self._pending_queue_entry_id = None
        if self._pending_queue_cancel is not None and (
            snapshot.queued_track_id is None or not snapshot.can_cancel_queued_track
        ):
            self._pending_queue_cancel = None
        if not snapshot.playing and snapshot.track_id is None:
            self._pending_current = None
            self._pending_queue = None
            self._pending_queue_metadata = None
            self._pending_queue_entry_id = None
            self._pending_queue_cancel = None
            self._pending_transition = None
        if (
            self._pending_paused is not None
            and snapshot.paused == self._pending_paused
        ):
            self._pending_paused = None
        if trim_cache:
            self.trim_audio_cache(snapshot)

    def protected_audio_paths(
        self, snapshot: PlaybackSnapshot | None = None
    ) -> tuple[str, ...]:
        snapshot = snapshot or self.engine.snapshot()
        paths = {
            path
            for path in (
                snapshot.track_filepath,
                snapshot.queued_track_filepath,
                snapshot.transition_target_filepath,
            )
            if path
        }
        for key in (
            self._pending_current,
            self._pending_queue,
            self._pending_transition,
        ):
            track = self.tracks.get(key) if key is not None else None
            if track is not None:
                paths.add(track.metadata.filepath)
        return tuple(paths)

    def trim_audio_cache(self, snapshot: PlaybackSnapshot | None = None) -> None:
        self.audio_cache.trim(protected_paths=self.protected_audio_paths(snapshot))

    def _key_for_track(
        self, track_id: str | None, filepath: str | None
    ) -> str | None:
        if track_id is None:
            return None
        return next(
            (
                key for key, track in self.tracks.items()
                if track.metadata.id == track_id
                and (filepath is None or track.metadata.filepath == filepath)
            ),
            None,
        )

    def _queued_playback_metadata(
        self, snapshot: PlaybackSnapshot, key: str
    ) -> TrackMetadata:
        if self._pending_queue == key and self._pending_queue_metadata is not None:
            return self._pending_queue_metadata
        snapshot_key = self._key_for_track(
            snapshot.queued_track_id, snapshot.queued_track_filepath
        )
        metadata = self.engine.queued_track
        if snapshot_key == key and metadata is not None:
            return metadata
        return self._track(key).metadata

    def _audio_for(self, track: LibraryTrack) -> DecodedAudio:
        audio = self.audio_cache.get(track.metadata.filepath)
        if audio is None:
            raise ValueError("Track has not been loaded yet")
        return audio

    def _track(self, key: str) -> LibraryTrack:
        try:
            return self.tracks[key]
        except KeyError as exc:
            raise KeyError(f"Unknown track: {key}") from exc

    def _save_settings(self) -> None:
        self.settings_store.save(self.settings)


def _transition_mode(value: TransitionMode | str) -> TransitionMode:
    if isinstance(value, TransitionMode):
        return value
    return TransitionMode(str(value))


def _validate_entry_id(metadata: TrackMetadata, entry_id: str) -> None:
    if entry_id in ("intro", "outro"):
        return
    if entry_id.startswith("loop:"):
        loop_id = entry_id.removeprefix("loop:")
        if loop_id:
            metadata.loop_bounds(loop_id)
            return
    raise ValueError(f"Unknown start section: {entry_id}")
