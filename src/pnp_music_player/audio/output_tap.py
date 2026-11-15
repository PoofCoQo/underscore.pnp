"""Non-blocking fan-out of the rendered master bus to asynchronous outputs."""

from __future__ import annotations

from dataclasses import dataclass
from math import log10
from queue import Empty, SimpleQueue
from threading import Event, Lock

import numpy as np


@dataclass(frozen=True, slots=True)
class BufferStats:
    capacity_frames: int
    buffered_frames: int
    dropped_frames: int
    underrun_frames: int


@dataclass(frozen=True, slots=True)
class DiscordOutputSnapshot:
    volume_db: float
    muted: bool
    peak_left_dbfs: float
    peak_right_dbfs: float


class Float32StereoRingBuffer:
    """A bounded SPSC-style PCM buffer that never waits in the producer.

    The PortAudio callback is the producer. If the short critical section is
    busy, that callback drops its copy instead of waiting for a network thread.
    """

    def __init__(self, capacity_frames: int) -> None:
        if capacity_frames <= 0:
            raise ValueError("capacity_frames must be positive")
        self._samples = np.zeros((capacity_frames, 2), dtype=np.float32)
        self._capacity = capacity_frames
        self._read_at = 0
        self._write_at = 0
        self._size = 0
        self._dropped = 0
        self._underrun = 0
        self._lock = Lock()

    def push(
        self, samples: np.ndarray, *, gain: float = 1.0, clip: bool = False
    ) -> bool:
        if samples.dtype != np.float32 or samples.ndim != 2 or samples.shape[1] != 2:
            raise ValueError("samples must be float32 stereo PCM")
        frames = samples.shape[0]
        if frames == 0:
            return True
        if not self._lock.acquire(blocking=False):
            # Integer updates are atomic while the GIL is held. An approximate
            # diagnostic counter is preferable to blocking the audio callback.
            self._dropped += frames
            return False
        try:
            source = samples
            if frames >= self._capacity:
                self._dropped += self._size + frames - self._capacity
                source = samples[-self._capacity :]
                frames = self._capacity
                self._read_at = 0
                self._write_at = 0
                self._size = 0
            overflow = max(0, self._size + frames - self._capacity)
            if overflow:
                self._read_at = (self._read_at + overflow) % self._capacity
                self._size -= overflow
                self._dropped += overflow
            first = min(frames, self._capacity - self._write_at)
            first_destination = self._samples[
                self._write_at : self._write_at + first
            ]
            _copy_with_gain(first_destination, source[:first], gain, clip=clip)
            remaining = frames - first
            if remaining:
                _copy_with_gain(
                    self._samples[:remaining], source[first:], gain, clip=clip
                )
            self._write_at = (self._write_at + frames) % self._capacity
            self._size += frames
            return True
        finally:
            self._lock.release()

    def read_into(self, output: np.ndarray) -> int:
        """Read available PCM and zero-fill the rest; return real frame count."""
        if output.dtype != np.float32 or output.ndim != 2 or output.shape[1] != 2:
            raise ValueError("output must be float32 stereo PCM")
        requested = output.shape[0]
        output.fill(0.0)
        with self._lock:
            count = min(requested, self._size)
            first = min(count, self._capacity - self._read_at)
            output[:first] = self._samples[self._read_at : self._read_at + first]
            remaining = count - first
            if remaining:
                output[first:count] = self._samples[:remaining]
            self._read_at = (self._read_at + count) % self._capacity
            self._size -= count
            self._underrun += requested - count
            return count

    def clear(self) -> None:
        with self._lock:
            self._read_at = 0
            self._write_at = 0
            self._size = 0

    def stats(self) -> BufferStats:
        with self._lock:
            return BufferStats(
                capacity_frames=self._capacity,
                buffered_frames=self._size,
                dropped_frames=self._dropped,
                underrun_frames=self._underrun,
            )


class DiscordPcmTap:
    """Independent Discord gain stage and 20-ms PCM voice source."""

    SAMPLE_RATE = 48_000
    PACKET_FRAMES = 960
    PACKET_BYTES = 3_840

    def __init__(self, *, capacity_frames: int = 24_000) -> None:
        self.buffer = Float32StereoRingBuffer(capacity_frames)
        self._enabled = Event()
        self._packet = np.empty((self.PACKET_FRAMES, 2), dtype=np.float32)
        self._commands: SimpleQueue[tuple[str, object]] = SimpleQueue()
        self._volume_db = -12.0
        self._gain = 10.0 ** (-12.0 / 20.0)
        self._muted = False
        self._peak_left_dbfs = -80.0
        self._peak_right_dbfs = -80.0

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.buffer.clear()
            self._enabled.set()
        else:
            self._enabled.clear()
            self.buffer.clear()

    def set_volume_db(self, value: float) -> None:
        value = float(value)
        if not -80.0 <= value <= 0.0:
            raise ValueError("Discord volume must be between -80 and 0 dB")
        self._commands.put(("volume", value))

    def set_muted(self, muted: bool) -> None:
        self._commands.put(("mute", bool(muted)))

    def snapshot(self) -> DiscordOutputSnapshot:
        return DiscordOutputSnapshot(
            volume_db=self._volume_db,
            muted=self._muted,
            peak_left_dbfs=self._peak_left_dbfs,
            peak_right_dbfs=self._peak_right_dbfs,
        )

    def consume(self, samples: np.ndarray) -> None:
        """Apply Discord-only output control without waiting for its consumer."""
        self._apply_commands()
        if not self._enabled.is_set():
            self._peak_left_dbfs = -80.0
            self._peak_right_dbfs = -80.0
            return
        gain = 0.0 if self._muted else self._gain
        self._peak_left_dbfs = _scaled_peak_dbfs(samples[:, 0], gain)
        self._peak_right_dbfs = _scaled_peak_dbfs(samples[:, 1], gain)
        self.buffer.push(samples, gain=gain, clip=True)

    def read_packet(self) -> bytes:
        """Return one Discord PCM packet, including silence on underflow."""
        self.buffer.read_into(self._packet)
        # Discord expects interleaved little-endian signed 16-bit stereo PCM.
        pcm = np.rint(np.clip(self._packet, -1.0, 1.0) * 32767.0).astype("<i2")
        result = pcm.tobytes()
        if len(result) != self.PACKET_BYTES:  # pragma: no cover - invariant guard
            raise RuntimeError("invalid Discord PCM packet size")
        return result

    def _apply_commands(self) -> None:
        for _ in range(16):
            try:
                command, value = self._commands.get_nowait()
            except Empty:
                break
            if command == "volume":
                self._volume_db = float(value)
                self._gain = 10.0 ** (self._volume_db / 20.0)
            elif command == "mute":
                self._muted = bool(value)


def _copy_with_gain(
    destination: np.ndarray,
    source: np.ndarray,
    gain: float,
    *,
    clip: bool,
) -> None:
    if gain == 0.0:
        destination.fill(0.0)
    elif gain == 1.0:
        destination[:] = source
    else:
        np.multiply(source, gain, out=destination)
    if clip:
        np.clip(destination, -1.0, 1.0, out=destination)


def _scaled_peak_dbfs(channel: np.ndarray, gain: float) -> float:
    if channel.size == 0 or gain == 0.0:
        return -80.0
    peak = min(1.0, float(np.max(np.abs(channel))) * gain)
    return max(-80.0, 20.0 * log10(peak)) if peak > 0.0 else -80.0
