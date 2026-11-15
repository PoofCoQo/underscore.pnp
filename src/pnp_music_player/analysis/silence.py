"""Block-based leading/trailing silence proposals."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, slots=True)
class SilenceDetectionConfig:
    block_frames: int = 1_024
    start_threshold_dbfs: float = -55.0
    start_minimum_silence_ms: float = 200.0
    start_safety_margin_ms: float = 50.0
    end_threshold_dbfs: float = -60.0
    end_minimum_silence_ms: float = 400.0
    end_safety_margin_ms: float = 150.0


@dataclass(slots=True)
class SilenceDetectionResult:
    play_start: int
    play_end: int
    completely_silent: bool = False
    warnings: list[str] = field(default_factory=list)


def detect_silence_boundaries(
    samples: np.ndarray,
    sample_rate: int,
    config: SilenceDetectionConfig | None = None,
) -> SilenceDetectionResult:
    """Suggest markers by examining only contiguous edge silence.

    A block is considered audible when either channel exceeds the configured
    RMS threshold. This prevents a one-sided stereo signal from being trimmed.
    Marker precision is therefore one analysis block, adjusted by margins.
    """
    config = config or SilenceDetectionConfig()
    _validate_audio(samples, sample_rate, config.block_frames)
    total_frames = int(samples.shape[0])
    block_levels = _block_channel_rms(samples, config.block_frames)
    start_audible = np.max(block_levels, axis=1) > _db_to_amplitude(
        config.start_threshold_dbfs
    )
    end_audible = np.max(block_levels, axis=1) > _db_to_amplitude(
        config.end_threshold_dbfs
    )

    if not np.any(start_audible) and not np.any(end_audible):
        return SilenceDetectionResult(
            0,
            total_frames,
            completely_silent=True,
            warnings=["audio appears completely silent; using the full file"],
        )

    warnings: list[str] = []
    if np.any(start_audible):
        first_block = int(np.flatnonzero(start_audible)[0])
        detected_start = first_block * config.block_frames
    else:
        detected_start = 0
        warnings.append(
            "audio never exceeds the start threshold; preserving the file start"
        )
    if np.any(end_audible):
        last_block = int(np.flatnonzero(end_audible)[-1])
        detected_end = min((last_block + 1) * config.block_frames, total_frames)
    else:
        detected_end = total_frames
        warnings.append(
            "audio never exceeds the end threshold; preserving the file end"
        )

    minimum_start = _milliseconds_to_frames(
        config.start_minimum_silence_ms, sample_rate
    )
    minimum_end = _milliseconds_to_frames(
        config.end_minimum_silence_ms, sample_rate
    )
    start_margin = _milliseconds_to_frames(config.start_safety_margin_ms, sample_rate)
    end_margin = _milliseconds_to_frames(config.end_safety_margin_ms, sample_rate)

    play_start = (
        max(0, detected_start - start_margin)
        if detected_start >= minimum_start
        else 0
    )
    trailing_silence = total_frames - detected_end
    play_end = (
        min(total_frames, detected_end + end_margin)
        if trailing_silence >= minimum_end
        else total_frames
    )
    if play_start >= play_end:
        return SilenceDetectionResult(
            0,
            total_frames,
            warnings=["silence proposal was empty; using the full file"],
        )
    return SilenceDetectionResult(play_start, play_end, warnings=warnings)


def _block_channel_rms(samples: np.ndarray, block_frames: int) -> np.ndarray:
    block_count = (samples.shape[0] + block_frames - 1) // block_frames
    levels = np.empty((block_count, samples.shape[1]), dtype=np.float64)
    for block_index in range(block_count):
        start = block_index * block_frames
        block = samples[start : start + block_frames]
        levels[block_index] = np.sqrt(
            np.mean(np.square(block, dtype=np.float64), axis=0)
        )
    return levels


def _validate_audio(samples: np.ndarray, sample_rate: int, block_frames: int) -> None:
    if samples.ndim != 2 or samples.shape[0] == 0 or samples.shape[1] < 1:
        raise ValueError("samples must have shape (non-empty frames, channels)")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if block_frames <= 0:
        raise ValueError("block_frames must be positive")


def _db_to_amplitude(decibels: float) -> float:
    return float(10.0 ** (decibels / 20.0))


def _milliseconds_to_frames(milliseconds: float, sample_rate: int) -> int:
    return max(0, round(milliseconds * sample_rate / 1_000.0))
