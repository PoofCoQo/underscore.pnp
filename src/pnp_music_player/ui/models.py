"""Small dependency-free models shared by the QML bridge and tests."""

from __future__ import annotations

from enum import Enum
from math import isfinite
from typing import Iterable


class SnapMode(str, Enum):
    OFF = "off"
    BEAT = "beat"
    BAR = "bar"


class PreviewMode(str, Enum):
    STOPPED = "stopped"
    NORMAL = "normal"
    LOOP = "loop"
    BOUNDARY = "boundary"
    INTRO = "intro"
    OUTRO = "outro"


def frame_to_x(frame: int, visible_start: int, visible_end: int, width: float) -> float:
    """Map a frame to viewport pixels using one canonical linear transform."""
    if visible_end <= visible_start or not isfinite(width) or width <= 0:
        return 0.0
    return (int(frame) - visible_start) * width / (visible_end - visible_start)


def x_to_frame(x: float, visible_start: int, visible_end: int, width: float) -> int:
    """Map pixels back to a clamped integer frame (round-half-up)."""
    if visible_end <= visible_start or not isfinite(width) or width <= 0:
        return visible_start
    clamped = min(max(float(x), 0.0), width)
    value = visible_start + clamped * (visible_end - visible_start) / width
    return min(visible_end, max(visible_start, int(value + 0.5)))


def nearest_frame(frame: int, candidates: Iterable[int]) -> int:
    """Return the nearest candidate; an exact tie selects the earlier frame."""
    values = list(candidates)
    if not values:
        return int(frame)
    return min(values, key=lambda candidate: (abs(candidate - frame), candidate))


def shifted_beats(beats: Iterable[int], offset: int, total_frames: int) -> list[int]:
    return sorted({beat + offset for beat in beats if 0 <= beat + offset <= total_frames})


def bar_positions(beats: Iterable[int], beats_per_bar: int, downbeat_offset: int) -> list[int]:
    """Select bar starts from an ordered beat grid.

    ``downbeat_offset`` is the zero-based index of the first downbeat in the
    effective beat list. Negative values are normalized modulo the bar length.
    """
    values = sorted(set(beats))
    if not values or beats_per_bar <= 0:
        return []
    first = downbeat_offset % beats_per_bar
    return values[first::beats_per_bar]


def format_time(frame: int, sample_rate: int = 48_000) -> str:
    seconds = max(0, frame) / sample_rate
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"
