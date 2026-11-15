"""Decode supported local files before realtime playback starts."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from pathlib import Path

import numpy as np
import soundfile as sf

from pnp_music_player.audio.resampler import convert_to_internal

INTERNAL_SAMPLE_RATE = 48_000


class AudioLoadError(RuntimeError):
    """A source file could not be decoded into the internal format."""


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    """Immutable descriptor around fully decoded, contiguous PCM."""

    samples: np.ndarray
    source_sample_rate: int
    sample_rate: int = INTERNAL_SAMPLE_RATE

    def __post_init__(self) -> None:
        if self.samples.dtype != np.float32:
            raise ValueError("decoded samples must use float32")
        if self.samples.ndim != 2 or self.samples.shape[1] != 2:
            raise ValueError("decoded samples must be stereo")
        if not self.samples.flags.c_contiguous:
            raise ValueError("decoded samples must be C-contiguous")
        if self.samples.shape[0] == 0:
            raise ValueError("decoded audio must not be empty")
        if self.source_sample_rate <= 0 or self.sample_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.samples.flags.writeable = False

    @property
    def total_frames(self) -> int:
        return int(self.samples.shape[0])


def load_audio(path: str | Path, target_rate: int = INTERNAL_SAMPLE_RATE) -> DecodedAudio:
    """Decode WAV/FLAC/OGG using libsndfile and convert it offline."""
    source_path = Path(path).expanduser()
    if not source_path.is_file():
        raise AudioLoadError(f"audio file not found: {source_path}")
    try:
        samples, source_rate = sf.read(
            source_path, dtype="float32", always_2d=True
        )
    except (RuntimeError, OSError, ValueError) as exc:
        raise AudioLoadError(f"cannot decode {source_path}: {exc}") from exc
    if samples.shape[0] == 0:
        raise AudioLoadError(f"audio file contains no frames: {source_path}")
    try:
        converted = convert_to_internal(samples, int(source_rate), target_rate)
        return DecodedAudio(converted, int(source_rate), target_rate)
    except (ValueError, MemoryError) as exc:
        raise AudioLoadError(f"cannot convert {source_path}: {exc}") from exc


def estimate_decoded_bytes(
    path: str | Path, target_rate: int = INTERNAL_SAMPLE_RATE
) -> int:
    """Estimate final stereo-float32 residency without decoding the source."""
    source_path = Path(path).expanduser()
    try:
        info = sf.info(source_path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise AudioLoadError(f"cannot inspect {source_path}: {exc}") from exc
    if info.samplerate <= 0 or info.frames <= 0:
        raise AudioLoadError(f"audio file contains no frames: {source_path}")
    frames = ceil(info.frames * target_rate / info.samplerate)
    return frames * 2 * np.dtype(np.float32).itemsize
