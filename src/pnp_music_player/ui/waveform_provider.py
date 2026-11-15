"""Pixel-bounded reads from the multi-resolution waveform cache."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pnp_music_player.analysis.waveform import WaveformCacheData, load_waveform_cache


@dataclass(frozen=True, slots=True)
class WaveformSlice:
    start_frame: int
    end_frame: int
    bin_size: int
    first_bin: int
    values: np.ndarray


class WaveformProvider:
    """Return only bins intersecting the visible range.

    The smallest cache bin covering at least one viewport pixel is preferred.
    The returned count is therefore bounded by roughly ``pixel_width + 2`` and
    is independent of track duration.
    """

    def __init__(self, cache: WaveformCacheData) -> None:
        self.cache = cache

    @classmethod
    def open(
        cls, cache_path: str | Path, source_path: str | Path, sample_rate: int
    ) -> "WaveformProvider | None":
        cache = load_waveform_cache(cache_path, source_path, sample_rate)
        return cls(cache) if cache is not None else None

    def visible(self, start_frame: int, end_frame: int, pixel_width: int) -> WaveformSlice:
        start = max(0, int(start_frame))
        end = max(start + 1, int(end_frame))
        pixels = max(1, int(pixel_width))
        frames_per_pixel = (end - start) / pixels
        eligible = [size for size in self.cache.bin_sizes if size >= frames_per_pixel]
        bin_size = min(eligible) if eligible else max(self.cache.bin_sizes)
        level = self.cache.bin_sizes.index(bin_size)
        values = self.cache.levels[level]
        first = max(0, start // bin_size)
        last = min(values.shape[0], (end + bin_size - 1) // bin_size)
        return WaveformSlice(start, end, bin_size, first, values[first:last])
