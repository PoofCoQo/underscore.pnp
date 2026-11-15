"""Transition types and allocation-free gain-curve helpers."""

from __future__ import annotations

from enum import Enum
from math import pi

import numpy as np


class TransitionMode(Enum):
    NORMAL = "normal"
    HARD_CUT = "hard_cut"
    FADE = "fade"
    CROSSFADE = "crossfade"


def fill_equal_power_gains(
    start_frame: int,
    frame_count: int,
    total_frames: int,
    indices: np.ndarray,
    source_gains: np.ndarray,
    target_gains: np.ndarray,
) -> None:
    """Fill cos/sin gains using absolute transition frame positions."""
    if total_frames < 2:
        raise ValueError("equal-power curves require at least two frames")
    _fill_phase(start_frame, frame_count, total_frames, indices, source_gains)
    np.multiply(source_gains[:frame_count], pi / 2.0, out=source_gains[:frame_count])
    np.sin(source_gains[:frame_count], out=target_gains[:frame_count])
    np.cos(source_gains[:frame_count], out=source_gains[:frame_count])


def fill_fade_gains(
    start_frame: int,
    frame_count: int,
    total_frames: int,
    indices: np.ndarray,
    gains: np.ndarray,
    *,
    fade_in: bool,
) -> None:
    """Fill a smooth quarter-sine fade with exact endpoints."""
    if total_frames < 2:
        raise ValueError("fade curves require at least two frames")
    _fill_phase(start_frame, frame_count, total_frames, indices, gains)
    np.multiply(gains[:frame_count], pi / 2.0, out=gains[:frame_count])
    if fade_in:
        np.sin(gains[:frame_count], out=gains[:frame_count])
    else:
        np.cos(gains[:frame_count], out=gains[:frame_count])


def _fill_phase(
    start_frame: int,
    frame_count: int,
    total_frames: int,
    indices: np.ndarray,
    output: np.ndarray,
) -> None:
    if frame_count < 0 or start_frame < 0 or start_frame + frame_count > total_frames:
        raise ValueError("gain request lies outside the transition")
    np.add(indices[:frame_count], start_frame, out=output[:frame_count])
    np.divide(output[:frame_count], total_frames - 1, out=output[:frame_count])
