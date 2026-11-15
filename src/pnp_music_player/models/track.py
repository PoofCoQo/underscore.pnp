"""Non-destructive track metadata and marker validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Mapping


class TrackValidationError(ValueError):
    """Raised when playback markers cannot describe a valid track."""


@dataclass(slots=True)
class LoopRegion:
    """One named alternative loop stored in source-frame coordinates."""

    id: str
    name: str
    start: int
    end: int
    exit_points: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "LoopRegion":
        return cls(**dict(values))


@dataclass(slots=True)
class TrackMetadata:
    """Metadata stored next to an unchanged source audio file.

    All positions use frames in ``internal_sample_rate``. A frame contains one
    sample for every channel, so stereo frame 10 addresses both channel values.
    """

    id: str
    title: str
    filepath: str
    source_sha256: str | None = None
    artist: str = ""
    album: str = ""
    attribution: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    favorite: bool = False

    source_sample_rate: int = 48_000
    internal_sample_rate: int = 48_000

    play_start: int = 0
    loop_start: int = 0
    loop_end: int = 1
    main_loop_name: str = "Main Loop"
    main_loop_exit_points: list[int] = field(default_factory=list)
    play_end: int = 1
    intro_start: int | None = None
    intro_end: int | None = None
    outro_start: int | None = None
    outro_end: int | None = None
    alternate_loops: list[LoopRegion] = field(default_factory=list)

    # Automatic import suggestions. Manual playback markers above remain the
    # authoritative values once a user has edited an existing track.
    detected_play_start: int | None = None
    detected_play_end: int | None = None

    integrated_lufs: float | None = None
    target_lufs: float = -18.0
    requested_normalization_gain_db: float | None = None
    normalization_gain_db: float = 0.0
    loudness_offset_db: float = 0.0
    normalization_enabled: bool = True
    peak_dbfs: float | None = None
    sample_peak_dbfs: float | None = None

    bpm: float | None = None
    detected_bpm: float | None = None
    beat_positions: list[int] = field(default_factory=list)
    detected_beat_positions: list[int] = field(default_factory=list)
    beat_analysis_confidence: float | None = None
    beats_per_bar: int = 4
    beat_unit: int = 4
    downbeat_offset: int = 0
    beat_grid_offset_frames: int = 0
    tempo_is_variable: bool | None = False
    detected_tempo_is_variable: bool | None = None

    analysis_version: int | None = None
    waveform_cache_path: str | None = None

    default_transition: str = "normal"
    default_fade_duration: float = 2.5

    def validate(self, total_frames: int) -> None:
        """Validate sample rates and the complete marker ordering."""
        errors: list[str] = []
        marker_values = {
            "play_start": self.play_start,
            "loop_start": self.loop_start,
            "loop_end": self.loop_end,
            "play_end": self.play_end,
            "intro_start": self.effective_intro_start,
            "intro_end": self.effective_intro_end,
            "outro_start": self.effective_outro_start,
            "outro_end": self.effective_outro_end,
        }
        for name, value in marker_values.items():
            if type(value) is not int:
                errors.append(f"{name} must be an integer frame index")
        if type(total_frames) is not int:
            errors.append("total_frames must be an integer")
        if not self.id.strip():
            errors.append("id must not be empty")
        if not self.title.strip():
            errors.append("title must not be empty")
        if self.source_sha256 is not None and (
            not isinstance(self.source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
        ):
            errors.append("source_sha256 must be a lowercase SHA-256 digest")
        if self.source_sample_rate <= 0:
            errors.append("source_sample_rate must be positive")
        if self.internal_sample_rate <= 0:
            errors.append("internal_sample_rate must be positive")
        if total_frames <= 0:
            errors.append("total_frames must be positive")
        if self.play_start < 0:
            errors.append("play_start must be >= 0")
        if self.play_start > self.loop_start:
            errors.append("play_start must be <= loop_start")
        if not (
            self.play_start <= self.effective_intro_start
            <= self.effective_intro_end <= self.loop_start
        ):
            errors.append("markers must satisfy play_start <= intro_start <= intro_end <= loop_start")
        if self.loop_start >= self.loop_end:
            errors.append("loop_start must be < loop_end")
        if not self.main_loop_name.strip():
            errors.append("main_loop_name must not be empty")
        if self.loop_end > self.play_end:
            errors.append("loop_end must be <= play_end")
        self._validate_exit_points(
            self.main_loop_exit_points,
            self.loop_start,
            self.loop_end,
            "main_loop_exit_points",
            errors,
        )
        if not (
            self.loop_end <= self.effective_outro_start
            <= self.effective_outro_end <= self.play_end
        ):
            errors.append("markers must satisfy loop_end <= outro_start <= outro_end <= play_end")
        if self.play_end > total_frames:
            errors.append("play_end must be <= total_frames")
        loop_ids: set[str] = {"main"}
        for index, region in enumerate(self.alternate_loops):
            prefix = f"alternate_loops[{index}]"
            if not isinstance(region, LoopRegion):
                errors.append(f"{prefix} must be a LoopRegion")
                continue
            if not region.id.strip() or region.id == "main" or region.id in loop_ids:
                errors.append(f"{prefix}.id must be unique and must not be 'main'")
            loop_ids.add(region.id)
            if not region.name.strip():
                errors.append(f"{prefix}.name must not be empty")
            if type(region.start) is not int or type(region.end) is not int:
                errors.append(f"{prefix} boundaries must be integer frame indices")
            elif not self.play_start <= region.start < region.end <= self.play_end:
                errors.append(
                    f"{prefix} must satisfy play_start <= start < end <= play_end"
                )
            self._validate_exit_points(
                region.exit_points,
                region.start,
                region.end,
                f"{prefix}.exit_points",
                errors,
            )
        if self.beats_per_bar <= 0:
            errors.append("beats_per_bar must be positive")
        if self.beat_unit not in (1, 2, 4, 8, 16):
            errors.append("beat_unit must be one of 1, 2, 4, 8, 16")
        if self.default_fade_duration < 0:
            errors.append("default_fade_duration must be >= 0")
        if any(type(position) is not int for position in self.beat_positions):
            errors.append("beat_positions must contain integer frame indices")
        elif any(position < 0 or position > total_frames for position in self.beat_positions):
            errors.append("beat_positions must lie within the decoded audio")
        if any(type(position) is not int for position in self.detected_beat_positions):
            errors.append("detected_beat_positions must contain integer frame indices")
        elif any(
            position < 0 or position >= total_frames
            for position in self.detected_beat_positions
        ):
            errors.append("detected_beat_positions must lie within the decoded audio")
        if errors:
            raise TrackValidationError("; ".join(errors))

    @property
    def playback_gain_db(self) -> float:
        """Constant per-track gain before safety clipping and master gain."""
        normalization = self.normalization_gain_db if self.normalization_enabled else 0.0
        return normalization + self.loudness_offset_db

    @property
    def effective_intro_start(self) -> int:
        return self.play_start if self.intro_start is None else self.intro_start

    @property
    def effective_intro_end(self) -> int:
        return self.loop_start if self.intro_end is None else self.intro_end

    @property
    def effective_outro_start(self) -> int:
        return self.loop_end if self.outro_start is None else self.outro_start

    @property
    def effective_outro_end(self) -> int:
        return self.play_end if self.outro_end is None else self.outro_end

    def loop_bounds(self, loop_id: str) -> tuple[int, int]:
        if loop_id == "main":
            return self.loop_start, self.loop_end
        for region in self.alternate_loops:
            if region.id == loop_id:
                return region.start, region.end
        raise KeyError(f"unknown loop region: {loop_id}")

    def loop_name(self, loop_id: str) -> str:
        if loop_id == "main":
            return self.main_loop_name
        for region in self.alternate_loops:
            if region.id == loop_id:
                return region.name
        raise KeyError(f"unknown loop region: {loop_id}")

    def loop_exit_points(self, loop_id: str) -> tuple[int, ...]:
        if loop_id == "main":
            return tuple(self.main_loop_exit_points)
        for region in self.alternate_loops:
            if region.id == loop_id:
                return tuple(region.exit_points)
        raise KeyError(f"unknown loop region: {loop_id}")

    @staticmethod
    def _validate_exit_points(
        points: list[int],
        start: int,
        end: int,
        name: str,
        errors: list[str],
    ) -> None:
        if any(type(point) is not int for point in points):
            errors.append(f"{name} must contain integer frame indices")
            return
        if points != sorted(set(points)):
            errors.append(f"{name} must be sorted and unique")
        if any(point < start or point > end for point in points):
            errors.append(f"{name} must lie within its loop boundaries")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "TrackMetadata":
        """Create metadata from a persistence-layer mapping."""
        converted = dict(values)
        converted["alternate_loops"] = [
            item if isinstance(item, LoopRegion) else LoopRegion.from_dict(item)
            for item in converted.get("alternate_loops", [])
        ]
        return cls(**converted)
