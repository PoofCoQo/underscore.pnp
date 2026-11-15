"""Versioned, atomic multi-resolution stereo waveform cache."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event

import numpy as np

WAVEFORM_CACHE_VERSION = 1
DEFAULT_BIN_SIZES = (256, 1_024, 4_096, 16_384)


@dataclass(slots=True)
class WaveformCacheData:
    path: Path
    bin_sizes: tuple[int, ...]
    levels: dict[int, np.ndarray]
    reused: bool


def get_or_create_waveform_cache(
    source_path: str | Path,
    samples: np.ndarray,
    internal_sample_rate: int,
    cache_directory: str | Path = Path(".cache/waveform"),
    *,
    bin_sizes: tuple[int, ...] = DEFAULT_BIN_SIZES,
    force: bool = False,
    cancel_event: Event | None = None,
) -> WaveformCacheData:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"waveform source file not found: {source}")
    if samples.ndim != 2 or samples.shape[1] != 2 or samples.shape[0] == 0:
        raise ValueError("waveform samples must be non-empty stereo PCM")
    if internal_sample_rate <= 0:
        raise ValueError("internal_sample_rate must be positive")
    if not bin_sizes or any(size <= 0 for size in bin_sizes):
        raise ValueError("bin_sizes must contain positive values")

    cache_root = Path(cache_directory)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{_source_key(source)}.npz"
    if not force:
        cached = load_waveform_cache(cache_path, source, internal_sample_rate)
        if cached is not None and cached.bin_sizes == tuple(bin_sizes):
            cached.reused = True
            return cached

    levels: dict[int, np.ndarray] = {}
    for level, bin_size in enumerate(bin_sizes):
        _check_cancelled(cancel_event)
        levels[level] = build_min_max_level(samples, bin_size)
    _check_cancelled(cancel_event)
    _write_cache_atomic(
        cache_path,
        source,
        internal_sample_rate,
        tuple(bin_sizes),
        levels,
    )
    return WaveformCacheData(cache_path, tuple(bin_sizes), levels, reused=False)


def build_min_max_level(samples: np.ndarray, bin_size: int) -> np.ndarray:
    """Return ``(bins, channels, min/max)`` without caching full PCM."""
    if samples.ndim != 2 or samples.shape[0] == 0:
        raise ValueError("samples must have shape (non-empty frames, channels)")
    if bin_size <= 0:
        raise ValueError("bin_size must be positive")
    frame_count, channel_count = samples.shape
    bin_count = (frame_count + bin_size - 1) // bin_size
    result = np.empty((bin_count, channel_count, 2), dtype=np.float32)
    full_bins = frame_count // bin_size
    if full_bins:
        reshaped = samples[: full_bins * bin_size].reshape(
            full_bins, bin_size, channel_count
        )
        result[:full_bins, :, 0] = np.min(reshaped, axis=1)
        result[:full_bins, :, 1] = np.max(reshaped, axis=1)
    if full_bins < bin_count:
        tail = samples[full_bins * bin_size :]
        result[full_bins, :, 0] = np.min(tail, axis=0)
        result[full_bins, :, 1] = np.max(tail, axis=0)
    return result


def load_waveform_cache(
    cache_path: str | Path,
    source_path: str | Path,
    internal_sample_rate: int,
) -> WaveformCacheData | None:
    path = Path(cache_path)
    source = Path(source_path).expanduser().resolve()
    try:
        source_stat = source.stat()
        with np.load(path, allow_pickle=False) as archive:
            if int(archive["cache_version"]) != WAVEFORM_CACHE_VERSION:
                return None
            if int(archive["source_size"]) != source_stat.st_size:
                return None
            if int(archive["source_mtime_ns"]) != source_stat.st_mtime_ns:
                return None
            if int(archive["internal_sample_rate"]) != internal_sample_rate:
                return None
            bin_sizes = tuple(int(value) for value in archive["bin_sizes"].tolist())
            levels: dict[int, np.ndarray] = {}
            for level, _bin_size in enumerate(bin_sizes):
                values = np.asarray(archive[f"level_{level}"], dtype=np.float32)
                if values.ndim != 3 or values.shape[1:] != (2, 2):
                    return None
                levels[level] = values
    except (OSError, ValueError, KeyError, EOFError):
        return None
    return WaveformCacheData(path, bin_sizes, levels, reused=True)


def _write_cache_atomic(
    cache_path: Path,
    source_path: Path,
    internal_sample_rate: int,
    bin_sizes: tuple[int, ...],
    levels: dict[int, np.ndarray],
) -> None:
    source_stat = source_path.stat()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{cache_path.stem}.",
            suffix=".tmp",
            dir=cache_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            payload: dict[str, np.ndarray | int] = {
                "cache_version": WAVEFORM_CACHE_VERSION,
                "source_size": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns,
                "internal_sample_rate": internal_sample_rate,
                "bin_sizes": np.asarray(bin_sizes, dtype=np.int64),
            }
            payload.update(
                {f"level_{level}": values for level, values in levels.items()}
            )
            np.savez_compressed(temporary, **payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, cache_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _source_key(source_path: Path) -> str:
    return hashlib.sha256(os.fsencode(source_path)).hexdigest()[:24]


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("waveform analysis cancelled")
