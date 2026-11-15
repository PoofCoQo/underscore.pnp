"""Offline channel and sample-rate conversion."""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly


def to_stereo(samples: np.ndarray) -> np.ndarray:
    """Convert a frames-by-channels float array to stereo.

    Mono is duplicated. For multichannel sources the first two channels are
    retained deliberately; automatic surround downmix coefficients would be a
    source-format policy and are outside phase 1.
    """
    if samples.ndim != 2 or samples.shape[1] < 1:
        raise ValueError("audio samples must have shape (frames, channels)")
    if samples.shape[1] == 1:
        return np.ascontiguousarray(np.repeat(samples, 2, axis=1), dtype=np.float32)
    return np.ascontiguousarray(samples[:, :2], dtype=np.float32)


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Offline polyphase-resample frames while preserving channel layout."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if samples.ndim != 2:
        raise ValueError("audio samples must have shape (frames, channels)")
    if source_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    divisor = gcd(source_rate, target_rate)
    converted = resample_poly(
        samples,
        up=target_rate // divisor,
        down=source_rate // divisor,
        axis=0,
        padtype="line",
    )
    return np.ascontiguousarray(converted, dtype=np.float32)


def convert_to_internal(
    samples: np.ndarray, source_rate: int, target_rate: int = 48_000
) -> np.ndarray:
    """Normalize decoded PCM to 48-kHz stereo float32 (by default)."""
    return resample(to_stereo(samples), source_rate, target_rate)

