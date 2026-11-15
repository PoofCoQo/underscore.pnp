"""Conservative librosa beat detection and tempo-variation hints."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import librosa
import numpy as np


@dataclass(frozen=True, slots=True)
class BeatAnalysisConfig:
    hop_length: int = 512
    minimum_beats: int = 3
    tempo_variation_relative_mad_threshold: float = 0.08
    minimum_intervals_for_variation: int = 4


@dataclass(slots=True)
class BeatAnalysisResult:
    bpm: float | None
    beat_positions: list[int]
    confidence: float | None = None
    tempo_is_variable: bool | None = None
    warnings: list[str] = field(default_factory=list)


def analyze_beats(
    samples: np.ndarray,
    sample_rate: int,
    config: BeatAnalysisConfig | None = None,
) -> BeatAnalysisResult:
    config = config or BeatAnalysisConfig()
    if samples.ndim != 2 or samples.shape[0] == 0:
        raise ValueError("samples must have shape (non-empty frames, channels)")
    mono = np.mean(samples, axis=1, dtype=np.float32)
    if float(np.max(np.abs(mono))) < 1e-7:
        return BeatAnalysisResult(
            None, [], warnings=["no usable beat signal detected"]
        )
    try:
        tempo, beat_frames = librosa.beat.beat_track(
            y=mono,
            sr=sample_rate,
            hop_length=config.hop_length,
            units="frames",
        )
    except Exception as exc:
        return BeatAnalysisResult(None, [], warnings=[f"beat analysis failed: {exc}"])

    tempo_value = float(np.asarray(tempo).reshape(-1)[0]) if np.size(tempo) else 0.0
    positions = librosa.frames_to_samples(
        np.asarray(beat_frames), hop_length=config.hop_length
    )
    valid_positions = sorted(
        {
            int(position)
            for position in positions
            if 0 <= int(position) < samples.shape[0]
        }
    )
    if (
        len(valid_positions) < config.minimum_beats
        or not isfinite(tempo_value)
        or tempo_value <= 0
    ):
        return BeatAnalysisResult(
            None,
            [],
            warnings=["beat analysis did not produce a reliable beat sequence"],
        )
    return BeatAnalysisResult(
        bpm=tempo_value,
        beat_positions=valid_positions,
        confidence=None,
        tempo_is_variable=detect_variable_tempo(valid_positions, config),
    )


def detect_variable_tempo(
    beat_positions: list[int] | np.ndarray,
    config: BeatAnalysisConfig | None = None,
) -> bool | None:
    """Return a hint from robust MAD and interquartile interval spread."""
    config = config or BeatAnalysisConfig()
    positions = np.asarray(beat_positions, dtype=np.int64)
    intervals = np.diff(positions)
    if intervals.size < config.minimum_intervals_for_variation:
        return None
    median_interval = float(np.median(intervals))
    if median_interval <= 0:
        return None
    relative_mad = float(
        np.median(np.abs(intervals - median_interval)) / median_interval
    )
    quartile_25, quartile_75 = np.percentile(intervals, (25, 75))
    relative_iqr = float((quartile_75 - quartile_25) / median_interval)
    return max(relative_mad, relative_iqr) > (
        config.tempo_variation_relative_mad_threshold
    )
