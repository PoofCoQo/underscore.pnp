"""Qt view-model exposing Phase-6 player actions and snapshots to QML."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import platform
from threading import Event

from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence

from pnp_music_player import __version__
from pnp_music_player.config_env import (
    DISCORD_TOKEN_ENV,
    default_env_file,
    load_discord_token,
    save_discord_token,
)
from pnp_music_player.audio.loader import DecodedAudio, load_audio
from pnp_music_player.logging_config import (
    current_log_directory,
    diagnostic_logging_enabled,
    register_secret,
    set_diagnostic_logging,
)
from pnp_music_player.playback.state_machine import PlaybackState
from pnp_music_player.ui.audio_runtime import AudioRuntime
from pnp_music_player.ui.persistence import MetadataStore
from pnp_music_player.ui.player_controller import (
    LibraryTrack,
    PlayerController,
    load_library,
)
from pnp_music_player.ui.player_settings import (
    DEFAULT_KEYBINDINGS,
    PlayerSettingsStore,
)
from pnp_music_player.ui.track_model import TrackListModel
from pnp_music_player.ui.worker_thread import WorkerThread

_LOG = logging.getLogger("ui.player")

_KEYBINDING_LABELS = {
    "pause_resume": "Pause / Resume",
    "stop": "Stop",
    "go_now": "Go Now",
    "transition_normal": "Normal transition",
    "transition_cut": "Cut transition",
    "transition_fade": "Fade transition",
    "transition_crossfade": "Crossfade transition",
    "mute_local": "Mute local output",
    "mute_discord": "Mute Discord output",
    "fullscreen": "Toggle fullscreen",
    "favorite_1": "Queue favorite 1",
    "favorite_2": "Queue favorite 2",
    "favorite_3": "Queue favorite 3",
    "favorite_4": "Queue favorite 4",
    "favorite_5": "Queue favorite 5",
    "favorite_6": "Queue favorite 6",
    "favorite_7": "Queue favorite 7",
    "favorite_8": "Queue favorite 8",
    "favorite_9": "Queue favorite 9",
    "favorite_10": "Queue favorite 10",
}


class AudioLoadWorker(QObject):
    loaded = Signal(str, object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, key: str, filepath: str) -> None:
        super().__init__()
        self.key = key
        self.filepath = filepath
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            audio = load_audio(self.filepath)
            if not self.cancel_event.is_set():
                self.loaded.emit(self.key, audio)
        except Exception as exc:
            if not self.cancel_event.is_set():
                _LOG.exception("cannot load player track: %s", self.filepath)
                self.failed.emit(self.key, str(exc))
        finally:
            self.finished.emit()


class PlayerBridge(QObject):
    changed = Signal()
    libraryChanged = Signal()
    playbackChanged = Signal()
    loopsChanged = Signal()
    outputsChanged = Signal()
    discordChanged = Signal()

    def __init__(
        self,
        runtime: AudioRuntime,
        metadata_store: MetadataStore,
        settings_path: Path,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.metadata_store = metadata_store
        self._metadata_problems = []
        tracks = load_library(metadata_store, problems=self._metadata_problems)
        self.controller = PlayerController(
            runtime.engine,
            tracks,
            PlayerSettingsStore(settings_path),
            audio_cache=runtime.audio_cache,
            discord_output=runtime.discord_tap,
        )
        self.track_model = TrackListModel()
        self.track_model.set_tracks(list(self.controller.tracks.values()))
        self.track_model.set_category(self.controller.current_category)
        self.track_model.set_album(self.controller.current_album)
        self._snapshot = runtime.engine.snapshot()
        self._discord_output_snapshot = runtime.discord_tap.snapshot()
        self._error = ""
        self._loading_key: str | None = None
        self._closing = False
        self._suppress_loaded_track_action = False
        self._thread: QThread | None = None
        self._worker: AudioLoadWorker | None = None
        self._outputs: list[dict[str, object]] = []
        self._token_status = ""
        self._diagnostic_status = ""
        if self.controller.settings.audio_cache_mb is None:
            self.controller.settings.audio_cache_mb = max(
                64,
                min(
                    2048,
                    round(runtime.audio_cache.stats().byte_limit / (1024 * 1024)),
                ),
            )
            self.controller.persist_settings()
        else:
            runtime.audio_cache.set_byte_limit(
                self.controller.settings.audio_cache_mb * 1024 * 1024,
                protected_paths=runtime.protected_audio_paths(),
            )
        self.runtime.discord.set_on_change(self.discordChanged.emit)
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(400)
        self._settings_timer.timeout.connect(self.controller.persist_settings)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        self.refreshOutputs()
        try:
            selected, warning = self.runtime.select_output(
                self.controller.settings.selected_output
            )
            self.controller.settings.selected_output = selected
            self.controller.persist_settings()
            if warning:
                self._error = warning
        except Exception as exc:
            self._error = f"Audio output is currently unavailable: {exc}"

    @property
    def worker_running(self) -> bool:
        return self._thread is not None

    def begin_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._suppress_loaded_track_action = True
        self._poll_timer.stop()
        self.runtime.discord.set_on_change(None)
        if self._settings_timer.isActive():
            self._settings_timer.stop()
            try:
                self.controller.persist_settings()
            except OSError:
                _LOG.exception("could not save settings during shutdown")
        if self._worker is not None:
            self._worker.cancel()

    def close(self) -> None:
        self.begin_shutdown()
        if self._thread is not None:
            self._thread.wait()

    @Property(QObject, constant=True)
    def tracks(self) -> QObject:
        return self.track_model

    @Property("QVariantList", notify=libraryChanged)
    def categories(self) -> list[str]:
        return self.controller.categories

    @Property(str, notify=libraryChanged)
    def currentCategory(self) -> str:
        return self.controller.current_category

    @Property("QVariantList", notify=libraryChanged)
    def albums(self) -> list[str]:
        return self.controller.albums

    @Property(str, notify=libraryChanged)
    def currentAlbum(self) -> str:
        return self.controller.current_album

    @Property(bool, notify=libraryChanged)
    def libraryEmpty(self) -> bool:
        return not self.controller.tracks

    @Property("QVariantList", notify=libraryChanged)
    def metadataProblems(self) -> list[dict[str, str]]:
        return [
            {"path": str(problem.path), "reason": problem.reason}
            for problem in self._metadata_problems
        ]

    @Property(str, notify=playbackChanged)
    def currentTitle(self) -> str:
        track = self._current_track()
        return track.metadata.title if track else "—"

    @Property(str, notify=playbackChanged)
    def currentDetails(self) -> str:
        track = self._current_track()
        if not track:
            return "No track active"
        meta = track.metadata
        return " · ".join(
            part for part in (meta.artist, meta.category, " · ".join(meta.tags[:3]))
            if part
        )

    @Property(str, notify=playbackChanged)
    def queuedTitle(self) -> str:
        track = self._queued_track()
        return track.metadata.title if track else "—"

    @Property("QVariantList", notify=playbackChanged)
    def queuedParts(self) -> list[dict[str, object]]:
        return self.controller.queued_parts(self._snapshot)

    @Property(str, notify=playbackChanged)
    def queuedEntryId(self) -> str:
        return self.controller.queued_entry_id(self._snapshot) or ""

    @Property(bool, notify=playbackChanged)
    def canSelectQueuedPart(self) -> bool:
        return (
            self.controller.queued_key(self._snapshot) is not None
            and not self.controller.transition_in_flight(self._snapshot)
        )

    @Property(bool, notify=playbackChanged)
    def canCancelQueue(self) -> bool:
        return self.controller.can_cancel_queue(self._snapshot)

    @Property(str, notify=playbackChanged)
    def playbackState(self) -> str:
        names = {
            PlaybackState.STOPPED: "STOPPED",
            PlaybackState.PLAYING_INTRO: "INTRO",
            PlaybackState.LOOPING: "LOOP",
            PlaybackState.EXITING_LOOP: "EXITING LOOP",
            PlaybackState.PLAYING_OUTRO: "OUTRO",
            PlaybackState.FADING_OUT: "FADE OUT",
            PlaybackState.FADING_IN: "FADE IN",
            PlaybackState.CROSSFADING: "CROSSFADE",
            PlaybackState.PAUSED: "PAUSED",
        }
        return names[self._snapshot.state]

    @Property(bool, notify=playbackChanged)
    def playing(self) -> bool:
        return self._snapshot.playing

    @Property(bool, notify=playbackChanged)
    def paused(self) -> bool:
        return self.controller.effective_paused(self._snapshot)

    @Property(int, notify=playbackChanged)
    def loopCount(self) -> int:
        return self._snapshot.loop_count

    @Property(str, notify=playbackChanged)
    def nextJumpText(self) -> str:
        frames = max(0, self._snapshot.next_jump_frames)
        return self._format_frames(frames)

    @Property(bool, notify=playbackChanged)
    def hasNextExit(self) -> bool:
        return self._snapshot.next_exit_frames is not None

    @Property(str, notify=playbackChanged)
    def nextExitText(self) -> str:
        frames = self._snapshot.next_exit_frames
        return self._format_frames(max(0, frames)) if frames is not None else ""

    def _format_frames(self, frames: int) -> str:
        seconds = frames / self.runtime.engine.sample_rate
        minutes = int(seconds // 60)
        return f"{minutes:02d}:{seconds - minutes * 60:04.1f}"

    @Property("QVariantList", notify=loopsChanged)
    def currentLoops(self) -> list[dict[str, object]]:
        metadata = self.runtime.engine.current_track
        if metadata is None:
            return []
        regions = [
            {"id": "main", "name": metadata.main_loop_name, "main": True},
            *[
                {"id": region.id, "name": region.name, "main": False}
                for region in metadata.alternate_loops
            ],
        ]
        for region in regions:
            loop_id = str(region["id"])
            region["active"] = loop_id == self._snapshot.active_loop_id
            region["pending"] = loop_id == self._snapshot.pending_loop_id
        return regions

    @Property(str, notify=loopsChanged)
    def activeLoopName(self) -> str:
        metadata = self.runtime.engine.current_track
        if metadata is None:
            return "—"
        try:
            return metadata.loop_name(self._snapshot.active_loop_id)
        except KeyError:
            return "Main Loop"

    @Property(str, notify=loopsChanged)
    def pendingLoopName(self) -> str:
        metadata = self.runtime.engine.current_track
        if metadata is None or self._snapshot.pending_loop_id is None:
            return ""
        try:
            return metadata.loop_name(self._snapshot.pending_loop_id)
        except KeyError:
            return ""

    @Property(float, notify=playbackChanged)
    def peakLeftDb(self) -> float:
        return self._snapshot.output_peak_left_dbfs

    @Property(float, notify=playbackChanged)
    def peakRightDb(self) -> float:
        return self._snapshot.output_peak_right_dbfs

    @Property(float, notify=changed)
    def masterDb(self) -> float:
        return self.controller.master_db

    @Property(bool, notify=changed)
    def muted(self) -> bool:
        return self.controller.muted

    @Property(float, notify=changed)
    def discordDb(self) -> float:
        return self.controller.discord_db

    @Property(bool, notify=changed)
    def discordMuted(self) -> bool:
        return self.controller.discord_muted

    @Property(float, notify=playbackChanged)
    def discordPeakLeftDb(self) -> float:
        return self.runtime.discord_tap.snapshot().peak_left_dbfs

    @Property(float, notify=playbackChanged)
    def discordPeakRightDb(self) -> float:
        return self.runtime.discord_tap.snapshot().peak_right_dbfs

    @Property(str, notify=changed)
    def transitionMode(self) -> str:
        return self.controller.transition_mode.value

    @Property(float, notify=changed)
    def transitionDuration(self) -> float:
        return self.controller.transition_duration

    @Property(bool, notify=changed)
    def canGoNow(self) -> bool:
        return (
            self.controller.queued_key(self._snapshot) is not None
            and self.controller.transition_mode.value != "normal"
            and not self.controller.transition_in_flight(self._snapshot)
        )

    @Property(bool, notify=changed)
    def transitionActive(self) -> bool:
        return self.controller.transition_in_flight(self._snapshot)

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading_key is not None

    @Property(str, notify=changed)
    def errorMessage(self) -> str:
        return self._error

    @Property(str, constant=True)
    def settingsRecoveryMessage(self) -> str:
        return self.controller.settings_store.recovery_message

    @Property(str, notify=changed)
    def audioCacheText(self) -> str:
        stats = self.runtime.audio_cache.stats()
        used = stats.bytes_used / (1024 * 1024)
        limit = stats.byte_limit / (1024 * 1024)
        suffix = "Track" if stats.entries == 1 else "Tracks"
        return f"RAM Cache: {used:.0f} / {limit:.0f} MiB · {stats.entries} {suffix}"

    @Property(str, notify=changed)
    def audioCacheShortText(self) -> str:
        stats = self.runtime.audio_cache.stats()
        used = stats.bytes_used / (1024 * 1024)
        limit = stats.byte_limit / (1024 * 1024)
        return f"RAM {used:.0f} / {limit:.0f} MiB"

    @Property(int, notify=changed)
    def audioCacheMb(self) -> int:
        return int(self.controller.settings.audio_cache_mb or 384)

    @Property(str, notify=changed)
    def logDirectory(self) -> str:
        return str(current_log_directory())

    @Property(bool, notify=changed)
    def diagnosticLogging(self) -> bool:
        return diagnostic_logging_enabled()

    @Property(str, notify=changed)
    def diagnosticStatus(self) -> str:
        return self._diagnostic_status

    @Property("QVariantList", notify=outputsChanged)
    def availableOutputs(self) -> list[dict[str, object]]:
        return self._outputs

    @Property(str, notify=outputsChanged)
    def selectedOutput(self) -> str:
        return self.runtime.selected_output

    @Property(str, notify=discordChanged)
    def discordState(self) -> str:
        return self.runtime.discord.snapshot().state.value

    @Property(str, notify=discordChanged)
    def discordStatusText(self) -> str:
        return self.runtime.discord.snapshot().detail

    @Property("QVariantList", notify=discordChanged)
    def discordGuilds(self) -> list[dict[str, str]]:
        return [
            {"id": item.id, "name": item.name}
            for item in self.runtime.discord.snapshot().guilds
        ]

    @Property("QVariantList", notify=discordChanged)
    def discordChannels(self) -> list[dict[str, str]]:
        return [
            {"id": item.id, "name": item.name}
            for item in self.runtime.discord.snapshot().channels
        ]

    @Property(str, notify=discordChanged)
    def selectedDiscordGuild(self) -> str:
        return self.runtime.discord.snapshot().selected_guild_id

    @Property(bool, notify=discordChanged)
    def discordConnected(self) -> bool:
        return self.runtime.discord.snapshot().state.value == "in_channel"

    @Property(bool, notify=discordChanged)
    def discordFaderAvailable(self) -> bool:
        return self.runtime.discord.snapshot().gateway_connected

    @Property(bool, notify=discordChanged)
    def discordCanJoin(self) -> bool:
        snapshot = self.runtime.discord.snapshot()
        return bool(
            snapshot.selected_guild_id
            and snapshot.channels
            and snapshot.state.value in {"ready", "in_channel", "error"}
        )

    @Property(bool, notify=discordChanged)
    def discordCanInvite(self) -> bool:
        return bool(self.runtime.discord.snapshot().invite_url)

    @Property(bool, notify=discordChanged)
    def discordTokenConfigured(self) -> bool:
        return bool(load_discord_token())

    @Property(str, notify=discordChanged)
    def discordTokenLocation(self) -> str:
        return str(default_env_file())

    @Property(str, notify=discordChanged)
    def discordTokenStatus(self) -> str:
        return self._token_status

    @Property("QVariantList", notify=changed)
    def keybindings(self) -> list[dict[str, str]]:
        return [
            {
                "id": action,
                "label": _KEYBINDING_LABELS[action],
                "sequence": self.controller.settings.keybindings[action],
            }
            for action in DEFAULT_KEYBINDINGS
        ]

    @Property(bool, notify=changed)
    def fullscreenPreferred(self) -> bool:
        return self.controller.settings.fullscreen

    @Slot(str, result=str)
    def trackFileUrl(self, key: str) -> str:
        track = self.controller.tracks.get(key)
        if track is None:
            return ""
        return QUrl.fromLocalFile(track.metadata.filepath).toString()

    @Slot(str, result=str)
    def trackMetadataUrl(self, key: str) -> str:
        track = self.controller.tracks.get(key)
        if track is None:
            return ""
        document = self.metadata_store.document_for(track.metadata.filepath)
        return QUrl.fromLocalFile(str(document)).toString() if document.is_file() else ""

    @Slot(str)
    def selectCategory(self, category: str) -> None:
        self.controller.set_category(category)
        self.track_model.set_category(self.controller.current_category)
        self.libraryChanged.emit()

    @Slot(str)
    def selectAlbum(self, album: str) -> None:
        self.controller.set_album(album)
        self.track_model.set_album(self.controller.current_album)
        self.libraryChanged.emit()

    @Slot(str)
    def toggleFavorite(self, key: str) -> None:
        try:
            favorite = self.controller.toggle_favorite(key)
            track = self.controller.tracks[key]
            try:
                self.metadata_store.save(track.metadata)
            except Exception:
                track.metadata.favorite = not favorite
                raise
            self.track_model.set_tracks(list(self.controller.tracks.values()))
            self._update_model_status()
            self._error = ""
            self.libraryChanged.emit()
            self.changed.emit()
            _LOG.info("track favorite changed key=%s favorite=%s", key, favorite)
        except Exception as exc:
            _LOG.exception("could not save track favorite key=%s", key)
            self._error = f"Could not save favorite: {exc}"
            self.changed.emit()

    @Slot(str)
    def selectTrack(self, key: str) -> None:
        if self._closing or self._loading_key is not None:
            return
        try:
            track = self.controller.tracks[key]
            if not track.valid:
                self._error = track.error or "Track requires setup"
                self.changed.emit()
                return
            if self.controller.has_cached_audio(key):
                self._start_or_queue(track)
                return
            self.controller.make_room_for_load(key)
            self._loading_key = key
            self._suppress_loaded_track_action = False
            worker = AudioLoadWorker(key, track.metadata.filepath)
            thread = WorkerThread(worker, self)
            worker.loaded.connect(self._track_loaded)
            worker.failed.connect(self._track_failed)
            thread.finished.connect(self._load_finished)
            thread.finished.connect(thread.deleteLater)
            self._thread, self._worker = thread, worker
            thread.start()
            self.changed.emit()
        except Exception as exc:
            _LOG.exception("could not prepare selected track key=%s", key)
            self._error = str(exc)
            self.changed.emit()

    @Slot(int)
    def queueFavorite(self, position: int) -> None:
        key = self.controller.favorite_key_at(position)
        if key is None:
            _LOG.debug("favorite shortcut ignored position=%d", position)
            return
        _LOG.info("favorite shortcut selected position=%d key=%s", position, key)
        self.selectTrack(key)

    @Slot(str, object)
    def _track_loaded(self, key: str, audio: DecodedAudio) -> None:
        if self._closing:
            return
        track = self.controller.tracks.get(key)
        if track is None:
            return
        try:
            self.controller.store_audio(key, audio)
            if not self._suppress_loaded_track_action:
                self._start_or_queue(track)
        except Exception as exc:
            _LOG.exception("could not store decoded track key=%s", key)
            track.valid = False
            track.error = str(exc)
            self._error = str(exc)
            self.track_model.set_tracks(list(self.controller.tracks.values()))
            self.changed.emit()

    @Slot(str, str)
    def _track_failed(self, key: str, message: str) -> None:
        if self._closing:
            return
        _LOG.error("track loading failed key=%s error=%s", key, message)
        track = self.controller.tracks.get(key)
        if track:
            track.valid = False
            track.error = message
        self._error = message
        self.track_model.set_tracks(list(self.controller.tracks.values()))
        self.changed.emit()

    @Slot()
    def _load_finished(self) -> None:
        self._loading_key = None
        self._suppress_loaded_track_action = False
        self._thread = None
        self._worker = None
        if not self._closing:
            self.changed.emit()

    def _start_or_queue(self, track: LibraryTrack) -> None:
        if self._closing:
            return
        try:
            warning = self.runtime.ensure_started()
            self.controller.select_prepared(track)
            self._error = warning or ""
            self._update_model_status()
            self.changed.emit()
            self.playbackChanged.emit()
            snapshot = self.runtime.engine.snapshot()
            _LOG.info(
                "track selected key=%s state=%s current=%s queued=%s",
                track.key,
                snapshot.state.value,
                snapshot.track_id,
                snapshot.queued_track_id,
            )
        except Exception as exc:
            _LOG.exception("audio output unavailable while selecting key=%s", track.key)
            self._error = f"Audio output unavailable: {exc}"
            self.changed.emit()

    @Slot(str)
    def setTransitionMode(self, mode: str) -> None:
        try:
            self.controller.set_transition_mode(mode)
            self._error = ""
            _LOG.info("transition mode selected mode=%s", mode)
        except Exception as exc:
            _LOG.warning("invalid transition mode mode=%s error=%s", mode, exc)
            self._error = str(exc)
        self.changed.emit()

    @Slot(float)
    def setTransitionDuration(self, seconds: float) -> None:
        self.controller.set_transition_duration(seconds)
        self.changed.emit()

    @Slot()
    def goNow(self) -> None:
        _LOG.info("immediate transition requested mode=%s", self.transitionMode)
        self.controller.force_transition()
        self._update_model_status()
        self.changed.emit()
        self.playbackChanged.emit()

    @Slot()
    def pauseResume(self) -> None:
        self.controller.pause_resume()
        _LOG.info("playback pause toggled state=%s", self.runtime.engine.snapshot().state.value)
        self.changed.emit()
        self.playbackChanged.emit()

    @Slot()
    def stop(self) -> None:
        if self._loading_key is not None:
            self._suppress_loaded_track_action = True
        self.controller.stop()
        _LOG.info("playback stopped")
        self._update_model_status()
        self.changed.emit()
        self.playbackChanged.emit()

    @Slot(str)
    def selectLoop(self, loop_id: str) -> None:
        try:
            self.controller.select_loop(loop_id)
            self._error = ""
            _LOG.info("loop selected loop_id=%s", loop_id)
        except Exception as exc:
            _LOG.warning("loop selection rejected loop_id=%s error=%s", loop_id, exc)
            self._error = str(exc)
        self.changed.emit()
        self.playbackChanged.emit()

    @Slot(str)
    def selectQueuedEntry(self, entry_id: str) -> None:
        try:
            self.controller.select_queued_entry(entry_id)
            self._error = ""
        except Exception as exc:
            self._error = str(exc)
        self.changed.emit()
        self.playbackChanged.emit()

    @Slot(float)
    def setMasterDb(self, value: float) -> None:
        self.controller.set_master_db(value, persist=False)
        self._settings_timer.start()
        self.changed.emit()

    @Slot()
    def toggleMute(self) -> None:
        self.controller.set_muted(not self.controller.muted)
        self.changed.emit()

    @Slot(float)
    def setDiscordDb(self, value: float) -> None:
        self.controller.set_discord_db(value, persist=False)
        self._settings_timer.start()
        self.changed.emit()

    @Slot()
    def toggleDiscordMute(self) -> None:
        self.controller.set_discord_muted(not self.controller.discord_muted)
        self.changed.emit()

    @Slot()
    def refreshLibrary(self) -> None:
        self._metadata_problems = []
        tracks = load_library(self.metadata_store, problems=self._metadata_problems)
        self.controller.replace_library(tracks)
        self.track_model.set_tracks(list(self.controller.tracks.values()))
        self.track_model.set_category(self.controller.current_category)
        self.track_model.set_album(self.controller.current_album)
        self._update_model_status()
        self.libraryChanged.emit()

    @Slot()
    def refreshOutputs(self) -> None:
        try:
            self._outputs = [
                {"key": output.key, "label": output.label}
                for output in self.runtime.outputs()
            ]
            self._error = ""
        except Exception as exc:
            self._outputs = [{"key": "default", "label": "System Default"}]
            self._error = f"Could not read audio outputs: {exc}"
        self.outputsChanged.emit()
        self.changed.emit()

    @Slot(str)
    def selectOutput(self, key: str) -> None:
        try:
            selected, warning = self.runtime.select_output(key)
            self.controller.settings.selected_output = selected
            self.controller.persist_settings()
            self._error = warning or ""
        except Exception as exc:
            _LOG.exception("output selection failed")
            self._error = f"Could not switch audio output: {exc}"
        self.outputsChanged.emit()
        self.changed.emit()

    @Slot(str)
    def selectDiscordGuild(self, guild_id: str) -> None:
        self.runtime.discord.select_guild(guild_id)

    @Slot(str, str)
    def joinDiscordChannel(self, guild_id: str, channel_id: str) -> None:
        if guild_id and channel_id:
            self.runtime.discord.join(guild_id, channel_id)

    @Slot()
    def leaveDiscordChannel(self) -> None:
        self.runtime.discord.leave()

    @Slot()
    def addDiscordBotToServer(self) -> None:
        url = self.runtime.discord.snapshot().invite_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot(bool)
    def setDiagnosticLogging(self, enabled: bool) -> None:
        active = set_diagnostic_logging(enabled)
        self._diagnostic_status = (
            "Diagnostic logging is active for this session."
            if active else "Normal logging is active."
        )
        self.changed.emit()

    @Slot()
    def openLogFolder(self) -> None:
        try:
            directory = current_log_directory()
            directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                raise OSError("the operating system did not open the directory")
            self._diagnostic_status = "Log folder opened."
        except Exception as exc:
            _LOG.exception("could not open log directory")
            self._error = f"Could not open log folder: {exc}"
        self.changed.emit()

    @Slot()
    def copyDiagnosticSummary(self) -> None:
        stats = self.runtime.audio_cache.stats()
        output_names = ", ".join(
            str(item.get("label", "")) for item in self._outputs
        ) or "unavailable"
        summary = "\n".join(
            (
                "underscore.pnp diagnostic summary",
                f"Application: {__version__}",
                f"Python: {platform.python_version()} ({platform.machine()})",
                f"Qt: {qVersion()}",
                f"OS: {platform.platform()}",
                f"Data: {self.metadata_store.directory.parent}",
                f"Logs: {current_log_directory()}",
                f"Audio output: {self.runtime.selected_output}",
                f"Available outputs: {output_names}",
                f"Audio cache: {stats.bytes_used} / {stats.byte_limit} bytes, "
                f"{stats.entries} tracks",
                f"Discord state: {self.runtime.discord.snapshot().state.value}",
                f"Diagnostic logging: {diagnostic_logging_enabled()}",
            )
        )
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(summary)
        self._diagnostic_status = "Diagnostic summary copied to the clipboard."
        _LOG.info("diagnostic summary copied to clipboard")
        self.changed.emit()

    @Slot()
    def clearError(self) -> None:
        self._error = ""
        self.changed.emit()

    @Slot(bool)
    def setFullscreenPreferred(self, fullscreen: bool) -> None:
        self.controller.settings.fullscreen = bool(fullscreen)
        self.controller.persist_settings()
        self.changed.emit()

    @Slot(str, result=str)
    def keybinding(self, action: str) -> str:
        return self.controller.settings.keybindings.get(action, "")

    @Slot(str, str, result=bool)
    def setKeybinding(self, action: str, sequence: str) -> bool:
        if action not in DEFAULT_KEYBINDINGS:
            self._error = f"Unknown keyboard action: {action}"
            self.changed.emit()
            return False
        sequence = str(sequence).strip()
        parsed = QKeySequence.fromString(sequence, QKeySequence.PortableText)
        canonical = parsed.toString(QKeySequence.PortableText)
        if sequence and parsed.isEmpty():
            self._error = f"Invalid key sequence: {sequence}"
            self.changed.emit()
            return False
        conflict = next(
            (
                other
                for other, assigned in self.controller.settings.keybindings.items()
                if other != action and assigned and assigned == canonical
            ),
            None,
        )
        if conflict is not None:
            self._error = (
                f"{canonical} is already assigned to "
                f"{_KEYBINDING_LABELS.get(conflict, conflict)}"
            )
            self.changed.emit()
            return False
        self.controller.settings.keybindings[action] = canonical
        self.controller.persist_settings()
        self._error = ""
        self.changed.emit()
        return True

    @Slot()
    def resetKeybindings(self) -> None:
        self.controller.settings.keybindings = dict(DEFAULT_KEYBINDINGS)
        self.controller.persist_settings()
        self._error = ""
        self.changed.emit()

    @Slot(int)
    def setAudioCacheMb(self, megabytes: int) -> None:
        megabytes = max(64, min(2048, int(megabytes)))
        self.controller.settings.audio_cache_mb = megabytes
        self.runtime.audio_cache.set_byte_limit(
            megabytes * 1024 * 1024,
            protected_paths=self.runtime.protected_audio_paths(),
        )
        self.controller.persist_settings()
        self.changed.emit()

    @Slot(str, result=bool)
    def saveDiscordToken(self, token: str) -> bool:
        try:
            register_secret(token)
            save_discord_token(token)
            effective_token = load_discord_token()
            self.runtime.discord.reconfigure_token(effective_token)
            if os.environ.get(DISCORD_TOKEN_ENV, "").strip():
                self._token_status = (
                    "Token file saved. The process environment token remains active."
                )
            elif effective_token:
                self._token_status = "Token saved securely and Discord restarted."
            else:
                self._token_status = "Token removed; Discord output is disabled."
            self._error = ""
            self.discordChanged.emit()
            self.changed.emit()
            return True
        except Exception as exc:
            _LOG.exception("could not save Discord token configuration")
            self._token_status = ""
            self._error = f"Could not save Discord token: {exc}"
            self.discordChanged.emit()
            self.changed.emit()
            return False

    @Slot()
    def _poll(self) -> None:
        snapshot = self.runtime.engine.snapshot()
        discord_output = self.runtime.discord_tap.snapshot()
        loop_signature = (
            snapshot.track_id,
            snapshot.track_filepath,
            snapshot.active_loop_id,
            snapshot.pending_loop_id,
        )
        previous_loop_signature = (
            self._snapshot.track_id,
            self._snapshot.track_filepath,
            self._snapshot.active_loop_id,
            self._snapshot.pending_loop_id,
        )
        signature = (
            snapshot.track_id, snapshot.track_filepath,
            snapshot.queued_track_id, snapshot.queued_track_filepath,
            snapshot.queued_entry_id, snapshot.transition_target_entry_id,
            snapshot.state,
            snapshot.loop_count, snapshot.muted,
            snapshot.next_jump_frames // 4_800,
            (-1 if snapshot.next_exit_frames is None
             else snapshot.next_exit_frames // 4_800),
            round(snapshot.output_peak_left_dbfs, 1),
            round(snapshot.output_peak_right_dbfs, 1),
            round(discord_output.peak_left_dbfs, 1),
            round(discord_output.peak_right_dbfs, 1),
            snapshot.active_loop_id, snapshot.pending_loop_id,
        )
        previous = (
            self._snapshot.track_id, self._snapshot.track_filepath,
            self._snapshot.queued_track_id, self._snapshot.queued_track_filepath,
            self._snapshot.queued_entry_id,
            self._snapshot.transition_target_entry_id,
            self._snapshot.state, self._snapshot.loop_count, self._snapshot.muted,
            self._snapshot.next_jump_frames // 4_800,
            (-1 if self._snapshot.next_exit_frames is None
             else self._snapshot.next_exit_frames // 4_800),
            round(self._snapshot.output_peak_left_dbfs, 1),
            round(self._snapshot.output_peak_right_dbfs, 1),
            round(self._discord_output_snapshot.peak_left_dbfs, 1),
            round(self._discord_output_snapshot.peak_right_dbfs, 1),
            self._snapshot.active_loop_id, self._snapshot.pending_loop_id,
        )
        self._snapshot = snapshot
        self._discord_output_snapshot = discord_output
        self.controller.synchronize(snapshot)
        if loop_signature != previous_loop_signature:
            self.loopsChanged.emit()
        if signature != previous:
            self._update_model_status()
            self.playbackChanged.emit()
            self.changed.emit()

    def _update_model_status(self) -> None:
        self.track_model.set_playback(
            self.controller.current_key(self._snapshot),
            self.controller.queued_key(self._snapshot),
        )

    def _current_track(self) -> LibraryTrack | None:
        key = self.controller.current_key(self._snapshot)
        return self.controller.tracks.get(key) if key else None

    def _queued_track(self) -> LibraryTrack | None:
        key = self.controller.queued_key(self._snapshot)
        return self.controller.tracks.get(key) if key else None
