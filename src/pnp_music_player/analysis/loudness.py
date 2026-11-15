"""Offline BS.1770 loudness, sample peak and static gain calculation."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite, log10

import numpy as np
import pyloudnorm as pyln


@dataclass(frozen=True, slots=True)
class LoudnessConfig:
    target_lufs: float = -18.0
    max_positive_normalization_gain_db: float = 6.0
    sample_peak_ceiling_dbfs: float = -1.0


@dataclass(slots=True)
class LoudnessResult:
    integrated_lufs: float | None
    sample_peak_dbfs: float
    requested_normalization_gain_db: float | None
    normalization_gain_db: float
    warnings: list[str] = field(default_factory=list)


def analyze_loudness(
    samples: np.ndarray,
    sample_rate: int,
    play_start: int = 0,
    play_end: int | None = None,
    config: LoudnessConfig | None = None,
) -> LoudnessResult:
    config = config or LoudnessConfig()
    end = samples.shape[0] if play_end is None else play_end
    if samples.ndim != 2 or not 0 <= play_start < end <= samples.shape[0]:
        raise ValueError("invalid effective loudness region")
    region = samples[play_start:end]
    peak_dbfs = calculate_sample_peak_dbfs(region)
    warnings: list[str] = []
    integrated_lufs: float | None
    if np.max(np.abs(region)) == 0.0:
        integrated_lufs = None
        warnings.append("integrated loudness is undefined for silent audio")
    else:
        try:
            measured = float(pyln.Meter(sample_rate).integrated_loudness(region))
            integrated_lufs = measured if isfinite(measured) else None
            if integrated_lufs is None:
                warnings.append("integrated loudness measurement was non-finite")
        except (ValueError, RuntimeError, FloatingPointError) as exc:
            integrated_lufs = None
            warnings.append(f"integrated loudness analysis failed: {exc}")
    requested, applied = calculate_normalization_gain(
        integrated_lufs, peak_dbfs, config
    )
    return LoudnessResult(
        integrated_lufs,
        peak_dbfs,
        requested,
        applied,
        warnings,
    )


def calculate_sample_peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    return float("-inf") if peak == 0.0 else 20.0 * log10(peak)


def calculate_normalization_gain(
    integrated_lufs: float | None,
    sample_peak_dbfs: float,
    config: LoudnessConfig | None = None,
) -> tuple[float | None, float]:
    config = config or LoudnessConfig()
    if integrated_lufs is None or not isfinite(integrated_lufs):
        return None, 0.0
    requested = config.target_lufs - integrated_lufs
    applied = min(requested, config.max_positive_normalization_gain_db)
    if isfinite(sample_peak_dbfs):
        applied = min(applied, config.sample_peak_ceiling_dbfs - sample_peak_dbfs)
    return requested, applied

