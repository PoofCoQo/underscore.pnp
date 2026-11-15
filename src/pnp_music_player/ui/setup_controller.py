"""Pure setup-mode application controller, independent of Qt."""

from __future__ import annotations

from dataclasses import replace
from math import ceil, floor
from pathlib import Path
from typing import Callable

import numpy as np

from pnp_music_player.analysis.pipeline import AnalysisConfig, ImportResult, analyze_track
from pnp_music_player.audio.engine import AudioEngine
from pnp_music_player.audio.loader import DecodedAudio
from pnp_music_player.models.track import LoopRegion, TrackMetadata
from pnp_music_player.ui.models import (
    PreviewMode,
    SnapMode,
    bar_positions,
    nearest_frame,
    shifted_beats,
)
from pnp_music_player.ui.persistence import MetadataStore

MARKERS = (
    "play_start", "intro_start", "intro_end", "loop_start",
    "loop_end", "outro_start", "outro_end", "play_end",
)


def copy_metadata(metadata: TrackMetadata) -> TrackMetadata:
    return replace(
        metadata,
        tags=list(metadata.tags),
        main_loop_exit_points=list(metadata.main_loop_exit_points),
        beat_positions=list(metadata.beat_positions),
        detected_beat_positions=list(metadata.detected_beat_positions),
        alternate_loops=[
            replace(region, exit_points=list(region.exit_points))
            for region in metadata.alternate_loops
        ],
    )


class SetupController:
    """Own the editable track copy and translate setup actions to the engine."""

    def __init__(
        self,
        store: MetadataStore,
        *,
        engine: AudioEngine | None = None,
        cache_directory: str | Path = Path(".cache/waveform"),
    ) -> None:
        self.store = store
        self.engine = engine
        self.cache_directory = Path(cache_directory)
        self.audio: DecodedAudio | None = None
        self.metadata: TrackMetadata | None = None
        self._saved: TrackMetadata | None = None
        self.snap_mode = SnapMode.OFF
        self.cursor_frame = 0
        self.preview_mode = PreviewMode.STOPPED
        self._preview_playhead_frame = 0
        self._preview_play_generation = 0
        self._preview_source_start = 0
        self._preview_first_length = 0
        self._preview_boundary_target_start = 0
        self.selected_loop_id = "main"

    @property
    def total_frames(self) -> int:
        return self.audio.total_frames if self.audio is not None else 0

    @property
    def dirty(self) -> bool:
        return self.metadata is not None and self.metadata != self._saved

    def existing_metadata(self, path: str | Path) -> TrackMetadata | None:
        return self.store.load(path)

    def clear(self) -> None:
        self.audio = None
        self.metadata = None
        self._saved = None
        self.cursor_frame = 0
        self.preview_mode = PreviewMode.STOPPED
        self._preview_playhead_frame = 0
        self._preview_play_generation = 0
        self._preview_source_start = 0
        self._preview_first_length = 0
        self._preview_boundary_target_start = 0
        self.selected_loop_id = "main"

    def analyze(
        self,
        path: str | Path,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ImportResult:
        existing = self.existing_metadata(path)
        result = analyze_track(
            path,
            existing_metadata=existing,
            config=AnalysisConfig(waveform_cache_directory=self.cache_directory),
            progress_callback=progress_callback,
        )
        self.accept_import(result, saved_metadata=existing)
        return result

    def accept_import(
        self,
        result: ImportResult,
        *,
        saved_metadata: TrackMetadata | None = None,
        preserve_saved: bool = False,
    ) -> None:
        result.metadata.validate(result.audio.total_frames)
        self.audio = result.audio
        self.metadata = copy_metadata(result.metadata)
        if not preserve_saved:
            self._saved = copy_metadata(saved_metadata) if saved_metadata else None
        self.cursor_frame = self.metadata.effective_intro_start
        self.preview_mode = PreviewMode.STOPPED
        self._preview_playhead_frame = self.metadata.effective_intro_start
        self._preview_play_generation = 0
        self.selected_loop_id = "main"

    def loop_regions(self) -> list[LoopRegion]:
        metadata = self._require_metadata()
        return [
            LoopRegion(
                "main", metadata.main_loop_name,
                metadata.loop_start, metadata.loop_end,
                list(metadata.main_loop_exit_points),
            ),
            *[
                replace(region, exit_points=list(region.exit_points))
                for region in metadata.alternate_loops
            ],
        ]

    def select_loop(self, loop_id: str) -> None:
        self._loop_region(loop_id)
        self.selected_loop_id = loop_id

    def add_loop(self) -> LoopRegion:
        metadata = self._require_metadata()
        used = {region.id for region in metadata.alternate_loops}
        number = 1
        while f"loop-{number}" in used:
            number += 1
        region = LoopRegion(
            id=f"loop-{number}",
            name=f"Loop {number + 1}",
            start=metadata.loop_start,
            end=metadata.loop_end,
        )
        metadata.alternate_loops.append(region)
        self.selected_loop_id = region.id
        return region

    def remove_selected_loop(self) -> bool:
        if self.selected_loop_id == "main":
            return False
        metadata = self._require_metadata()
        before = len(metadata.alternate_loops)
        metadata.alternate_loops = [
            region for region in metadata.alternate_loops
            if region.id != self.selected_loop_id
        ]
        removed = len(metadata.alternate_loops) != before
        if removed:
            self.selected_loop_id = "main"
        return removed

    def rename_selected_loop(self, name: str) -> None:
        value = name.strip()
        if not value:
            raise ValueError("Loop name cannot be empty")
        if self.selected_loop_id == "main":
            self._require_metadata().main_loop_name = value
            return
        self._loop_region(self.selected_loop_id).name = value

    def set_selected_loop_boundary(self, boundary: str, frame: int) -> int:
        region = self._loop_region(self.selected_loop_id)
        metadata = self._require_metadata()
        value = self.snap(frame)
        if boundary == "start":
            value = min(region.end - 1, max(metadata.play_start, value))
            if self.selected_loop_id == "main":
                self.set_marker("loop_start", value, use_snap=False)
            else:
                region.start = value
        elif boundary == "end":
            value = min(metadata.play_end, max(region.start + 1, value))
            if self.selected_loop_id == "main":
                self.set_marker("loop_end", value, use_snap=False)
            else:
                region.end = value
        else:
            raise ValueError("boundary must be 'start' or 'end'")
        self._discard_exit_points_outside_selected_loop()
        return value

    def selected_loop_exit_points(self) -> list[int]:
        metadata = self._require_metadata()
        if self.selected_loop_id == "main":
            return list(metadata.main_loop_exit_points)
        return list(self._loop_region(self.selected_loop_id).exit_points)

    def add_selected_loop_exit_point(self, frame: int) -> int:
        region = self._loop_region(self.selected_loop_id)
        if not region.start <= int(frame) <= region.end:
            raise ValueError("Exit point must lie within the selected loop")
        value = self.snap_selected_loop_frame(frame)
        points = self.selected_loop_exit_points()
        points.append(value)
        self._set_selected_loop_exit_points(sorted(set(points)))
        return value

    def remove_selected_loop_exit_point(self, frame: int) -> bool:
        points = self.selected_loop_exit_points()
        try:
            points.remove(int(frame))
        except ValueError:
            return False
        self._set_selected_loop_exit_points(points)
        return True

    def move_selected_loop_exit_point(self, old_frame: int, new_frame: int) -> int:
        points = self.selected_loop_exit_points()
        try:
            points.remove(int(old_frame))
        except ValueError as exc:
            raise ValueError("Exit point was not found") from exc
        value = self.snap_selected_loop_frame(new_frame)
        points.append(value)
        self._set_selected_loop_exit_points(sorted(set(points)))
        return value

    def snap_selected_loop_frame(self, frame: int) -> int:
        region = self._loop_region(self.selected_loop_id)
        value = min(region.end, max(region.start, int(frame)))
        if self.snap_mode is SnapMode.BEAT:
            candidates = self.effective_beats()
        elif self.snap_mode is SnapMode.BAR:
            candidates = self.bars()
        else:
            return value
        inside = [
            candidate for candidate in candidates
            if region.start <= candidate <= region.end
        ]
        return nearest_frame(value, inside) if inside else value

    def _set_selected_loop_exit_points(self, points: list[int]) -> None:
        metadata = self._require_metadata()
        if self.selected_loop_id == "main":
            metadata.main_loop_exit_points = list(points)
        else:
            self._loop_region(self.selected_loop_id).exit_points = list(points)

    def _discard_exit_points_outside_selected_loop(self) -> None:
        region = self._loop_region(self.selected_loop_id)
        self._set_selected_loop_exit_points(
            [
                point for point in self.selected_loop_exit_points()
                if region.start <= point <= region.end
            ]
        )

    def _loop_region(self, loop_id: str) -> LoopRegion:
        metadata = self._require_metadata()
        if loop_id == "main":
            return LoopRegion(
                "main", metadata.main_loop_name,
                metadata.loop_start, metadata.loop_end,
                list(metadata.main_loop_exit_points),
            )
        for region in metadata.alternate_loops:
            if region.id == loop_id:
                return region
        raise ValueError(f"Unknown loop: {loop_id}")

    def effective_beats(self) -> list[int]:
        metadata = self._require_metadata()
        source = metadata.beat_positions or metadata.detected_beat_positions
        if metadata.bpm is None:
            return shifted_beats(
                source, metadata.beat_grid_offset_frames, self.total_frames
            )

        # A manual BPM defines a regular grid. Keep its phase anchored at the
        # first analyzed/manual beat (or play_start when no beats were found),
        # then apply the independently editable grid offset.
        anchor = (source[0] if source else metadata.play_start)
        anchor += metadata.beat_grid_offset_frames
        interval = metadata.internal_sample_rate * 60.0 / metadata.bpm
        first_index = ceil((0 - anchor) / interval)
        last_index = floor((self.total_frames - anchor) / interval)
        return sorted(
            {
                min(
                    self.total_frames,
                    max(0, round(anchor + index * interval)),
                )
                for index in range(first_index, last_index + 1)
            }
        )

    def bars(self) -> list[int]:
        metadata = self._require_metadata()
        return bar_positions(
            self.effective_beats(), metadata.beats_per_bar, metadata.downbeat_offset
        )

    def set_snap_mode(self, mode: SnapMode | str) -> None:
        self.snap_mode = SnapMode(mode)

    def snap(self, frame: int) -> int:
        frame = min(self.total_frames, max(0, int(frame)))
        if self.snap_mode is SnapMode.BEAT:
            return nearest_frame(frame, self.effective_beats())
        if self.snap_mode is SnapMode.BAR:
            return nearest_frame(frame, self.bars())
        return frame

    def set_marker(self, name: str, frame: int, *, use_snap: bool = True) -> int:
        metadata = self._require_metadata()
        if name not in MARKERS:
            raise ValueError(f"unknown marker: {name}")
        value = self.snap(frame) if use_snap else min(self.total_frames, max(0, int(frame)))
        earliest_alternative_start = min(
            (region.start for region in metadata.alternate_loops),
            default=self.total_frames,
        )
        latest_alternative_end = max(
            (region.end for region in metadata.alternate_loops),
            default=0,
        )
        limits = {
            "play_start": (
                0,
                min(
                    metadata.effective_intro_end
                    if metadata.intro_start is None
                    else metadata.effective_intro_start,
                    earliest_alternative_start,
                ),
            ),
            "intro_start": (metadata.play_start, metadata.effective_intro_end),
            "intro_end": (metadata.effective_intro_start, metadata.loop_start),
            "loop_start": (metadata.effective_intro_end, metadata.loop_end - 1),
            "loop_end": (metadata.loop_start + 1, metadata.effective_outro_start),
            "outro_start": (metadata.loop_end, metadata.effective_outro_end),
            "outro_end": (metadata.effective_outro_start, metadata.play_end),
            "play_end": (
                max(
                    metadata.effective_outro_start
                    if metadata.outro_end is None
                    else metadata.effective_outro_end,
                    latest_alternative_end,
                ),
                self.total_frames,
            ),
        }
        lower, upper = limits[name]
        value = min(upper, max(lower, value))
        setattr(metadata, name, value)
        if name in {"loop_start", "loop_end"}:
            metadata.main_loop_exit_points = [
                point for point in metadata.main_loop_exit_points
                if metadata.loop_start <= point <= metadata.loop_end
            ]
        if name == "play_start" and metadata.intro_start is None:
            metadata.intro_start = value
        elif name == "play_end" and metadata.outro_end is None:
            metadata.outro_end = value
        return value

    def set_text(self, name: str, value: str) -> None:
        if name not in {"title", "artist", "album", "attribution", "category"}:
            raise ValueError(f"unknown text field: {name}")
        setattr(self._require_metadata(), name, str(value))

    def set_tags(self, text: str) -> None:
        self._require_metadata().tags = [tag.strip() for tag in text.split(",") if tag.strip()]

    def set_bpm(self, value: float | None) -> None:
        metadata = self._require_metadata()
        if value is not None and not 20.0 <= float(value) <= 400.0:
            raise ValueError("BPM must be between 20 and 400")
        metadata.bpm = None if value is None else float(value)

    def reset_bpm(self) -> None:
        self._require_metadata().bpm = None

    def adjust_bpm(self, delta: float) -> float:
        metadata = self._require_metadata()
        current = metadata.bpm if metadata.bpm is not None else metadata.detected_bpm
        if current is None:
            raise ValueError("No BPM reference is available for this track")
        adjusted = round(float(current) + float(delta), 3)
        self.set_bpm(adjusted)
        return adjusted

    def set_time_signature(self, beats_per_bar: int, beat_unit: int) -> None:
        if not 1 <= int(beats_per_bar) <= 32 or int(beat_unit) not in (1, 2, 4, 8, 16):
            raise ValueError("invalid time signature")
        metadata = self._require_metadata()
        metadata.beats_per_bar = int(beats_per_bar)
        metadata.beat_unit = int(beat_unit)

    def set_downbeat_offset(self, value: int) -> None:
        metadata = self._require_metadata()
        metadata.downbeat_offset = int(value) % metadata.beats_per_bar

    def shift_beat_grid(self, frames: int) -> None:
        self._require_metadata().beat_grid_offset_frames += int(frames)

    def set_loudness_offset(self, value: float) -> None:
        value = float(value)
        if not -24.0 <= value <= 24.0:
            raise ValueError("loudness offset must be between -24 and +24 dB")
        self._require_metadata().loudness_offset_db = value

    def set_normalization_enabled(self, enabled: bool) -> None:
        self._require_metadata().normalization_enabled = bool(enabled)

    def set_cursor(self, frame: int) -> int:
        self.cursor_frame = min(self.total_frames, max(0, int(frame)))
        return self.cursor_frame

    @property
    def has_saved_version(self) -> bool:
        return self._saved is not None

    def save(self, *, backup_existing: bool = False) -> Path:
        metadata = self._require_metadata()
        metadata.validate(self.total_frames)
        path = self.store.save(metadata, backup_existing=backup_existing)
        self._saved = copy_metadata(metadata)
        return path

    def discard(self) -> None:
        if self._saved is None:
            self.clear()
        else:
            self.metadata = copy_metadata(self._saved)
            self.cursor_frame = self.metadata.effective_intro_start
            valid_loop_ids = {
                "main", *(region.id for region in self.metadata.alternate_loops)
            }
            if self.selected_loop_id not in valid_loop_ids:
                self.selected_loop_id = "main"

    def preview_normal(self) -> None:
        metadata, audio, engine = self._playback_parts()
        cursor = min(
            metadata.effective_outro_end - 1,
            max(metadata.effective_intro_start, self.cursor_frame),
        )
        if metadata.effective_intro_end <= cursor < metadata.loop_start:
            cursor = metadata.loop_start
        if cursor < metadata.loop_end:
            generation = engine.snapshot().play_command_generation + 1
            engine.play(audio, metadata, start_frame=cursor)
            self._preview_source_start = 0
            self._preview_first_length = 0
            self._begin_preview(PreviewMode.NORMAL, cursor, generation)
        else:
            cursor = max(cursor, metadata.effective_outro_start)
            self._play_region(
                cursor, metadata.effective_outro_end, PreviewMode.NORMAL
            )
            return

    def preview_loop(self) -> None:
        metadata, audio, engine = self._playback_parts()
        preview = copy_metadata(metadata)
        region = self._loop_region(self.selected_loop_id)
        preview.play_start = region.start
        preview.loop_start = region.start
        preview.loop_end = region.end
        preview.intro_start = region.start
        preview.intro_end = region.start
        preview.outro_start = max(region.end, preview.effective_outro_start)
        preview.outro_end = max(preview.outro_start, preview.effective_outro_end)
        preview.main_loop_exit_points = []
        preview.alternate_loops = []
        generation = engine.snapshot().play_command_generation + 1
        engine.play(audio, preview, start_frame=region.start)
        self._preview_source_start = 0
        self._preview_first_length = 0
        self._begin_preview(PreviewMode.LOOP, region.start, generation)

    def preview_boundary(self, seconds: float = 1.5) -> None:
        metadata, audio, engine = self._playback_parts()
        region = self._loop_region(self.selected_loop_id)
        length = max(1, round(seconds * audio.sample_rate))
        before_start = max(region.start, region.end - length)
        after_end = min(region.end, region.start + length)
        samples = np.ascontiguousarray(
            np.concatenate(
                (audio.samples[before_start:region.end], audio.samples[region.start:after_end])
            ),
            dtype=np.float32,
        )
        preview_audio = DecodedAudio(samples, audio.source_sample_rate, audio.sample_rate)
        preview_metadata = self._preview_metadata(preview_audio.total_frames)
        generation = engine.snapshot().play_command_generation + 1
        engine.play(preview_audio, preview_metadata)
        self._preview_source_start = before_start
        self._preview_first_length = region.end - before_start
        self._preview_boundary_target_start = region.start
        self._begin_preview(PreviewMode.BOUNDARY, before_start, generation)

    def preview_intro(self) -> None:
        metadata = self._require_metadata()
        self._play_region(
            metadata.effective_intro_start,
            metadata.effective_intro_end,
            PreviewMode.INTRO,
        )

    def preview_outro(self) -> None:
        metadata = self._require_metadata()
        self._play_region(
            metadata.effective_outro_start,
            metadata.effective_outro_end,
            PreviewMode.OUTRO,
        )

    def preview_range_once(
        self, start: int, end: int, *, from_cursor: bool = False
    ) -> None:
        """Play a selected editor range linearly, without program jumps."""
        start, end = self._validate_preview_range(start, end)
        if from_cursor:
            start = min(end - 1, max(start, self.cursor_frame))
        self._play_region(start, end, PreviewMode.NORMAL)

    def preview_range_loop(self, start: int, end: int) -> None:
        """Repeat an arbitrary selected editor range."""
        start, end = self._validate_preview_range(start, end)
        metadata, audio, engine = self._playback_parts()
        preview = copy_metadata(metadata)
        preview.play_start = start
        preview.intro_start = start
        preview.intro_end = start
        preview.loop_start = start
        preview.loop_end = end
        preview.outro_start = end
        preview.outro_end = end
        preview.play_end = end
        preview.main_loop_exit_points = []
        preview.alternate_loops = []
        generation = engine.snapshot().play_command_generation + 1
        engine.play(audio, preview, start_frame=start)
        self._preview_source_start = 0
        self._preview_first_length = 0
        self._begin_preview(PreviewMode.LOOP, start, generation)

    def preview_range_boundary(
        self, start: int, end: int, seconds: float = 1.5
    ) -> None:
        """Repeat only the end-to-start seam of a selected editor range."""
        start, end = self._validate_preview_range(start, end)
        _metadata, audio, engine = self._playback_parts()
        length = max(1, round(seconds * audio.sample_rate))
        before_start = max(start, end - length)
        after_end = min(end, start + length)
        samples = np.ascontiguousarray(
            np.concatenate(
                (audio.samples[before_start:end], audio.samples[start:after_end])
            ),
            dtype=np.float32,
        )
        preview_audio = DecodedAudio(
            samples, audio.source_sample_rate, audio.sample_rate
        )
        generation = engine.snapshot().play_command_generation + 1
        engine.play(preview_audio, self._preview_metadata(preview_audio.total_frames))
        self._preview_source_start = before_start
        self._preview_first_length = end - before_start
        self._preview_boundary_target_start = start
        self._begin_preview(PreviewMode.BOUNDARY, before_start, generation)

    def pause_or_resume(self) -> None:
        engine = self._require_engine()
        if engine.snapshot().paused:
            engine.resume()
        else:
            engine.pause()

    def stop(self) -> None:
        self._require_engine().stop()
        self.preview_mode = PreviewMode.STOPPED

    def preview_playhead(self) -> int:
        if self.engine is None or self.metadata is None:
            return self._preview_playhead_frame
        if self.preview_mode is PreviewMode.STOPPED:
            return self._preview_playhead_frame
        snapshot = self.engine.snapshot()
        if snapshot.play_command_generation < self._preview_play_generation:
            return self._preview_playhead_frame
        if not snapshot.playing:
            self.preview_mode = PreviewMode.STOPPED
            return self._preview_playhead_frame
        position = snapshot.position
        if self.preview_mode is PreviewMode.BOUNDARY:
            if position < self._preview_first_length:
                position = self._preview_source_start + position
            else:
                position = (
                    self._preview_boundary_target_start
                    + position - self._preview_first_length
                )
        self._preview_playhead_frame = position
        return self._preview_playhead_frame

    @property
    def preview_playhead_frame(self) -> int:
        return self._preview_playhead_frame

    def _begin_preview(
        self, mode: PreviewMode, start: int, generation: int
    ) -> None:
        self._preview_playhead_frame = int(start)
        self._preview_play_generation = generation
        self.preview_mode = mode

    def _play_region(self, start: int, end: int, mode: PreviewMode) -> None:
        metadata, audio, engine = self._playback_parts()
        if end <= start:
            raise ValueError("preview region is empty")
        generation = engine.snapshot().play_command_generation + 1
        engine.play_once(audio, metadata, start_frame=start, end_frame=end)
        self._preview_source_start = 0
        self._preview_first_length = 0
        self._begin_preview(mode, start, generation)

    def _validate_preview_range(self, start: int, end: int) -> tuple[int, int]:
        start, end = int(start), int(end)
        if not 0 <= start < end <= self.total_frames:
            raise ValueError("Preview section lies outside the audio file")
        return start, end

    def _preview_metadata(self, frames: int) -> TrackMetadata:
        # Seam previews loop within the existing persistent output stream.
        base = self._require_metadata()
        return replace(
            base, id=f"{base.id}:preview", play_start=0, loop_start=0,
            loop_end=frames, play_end=frames, tags=list(base.tags),
            intro_start=None, intro_end=None, outro_start=None, outro_end=None,
            main_loop_exit_points=[],
            alternate_loops=[],
            beat_positions=[], detected_beat_positions=[],
        )

    def _playback_parts(self) -> tuple[TrackMetadata, DecodedAudio, AudioEngine]:
        return self._require_metadata(), self._require_audio(), self._require_engine()

    def _require_metadata(self) -> TrackMetadata:
        if self.metadata is None:
            raise RuntimeError("no track loaded")
        return self.metadata

    def _require_audio(self) -> DecodedAudio:
        if self.audio is None:
            raise RuntimeError("no track loaded")
        return self.audio

    def _require_engine(self) -> AudioEngine:
        if self.engine is None:
            raise RuntimeError("audio preview is unavailable")
        return self.engine
