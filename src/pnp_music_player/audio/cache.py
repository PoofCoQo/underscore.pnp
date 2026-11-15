"""Size-bounded shared cache for fully decoded playback audio."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Iterable

from pnp_music_player.audio.loader import DecodedAudio

DEFAULT_AUDIO_CACHE_BYTES = 384 * 1024 * 1024
AUDIO_CACHE_MB_ENV = "PNP_MUSIC_AUDIO_CACHE_MB"
_LOG = logging.getLogger("audio.cache")


@dataclass(frozen=True, slots=True)
class AudioCacheStats:
    entries: int
    bytes_used: int
    byte_limit: int
    pinned_entries: int


@dataclass(slots=True)
class _CacheEntry:
    audio: DecodedAudio
    source_signature: tuple[int, int] | None

    @property
    def byte_size(self) -> int:
        return int(self.audio.samples.nbytes)


class AudioCache:
    """Thread-safe LRU whose limit includes internally pinned entries.

    The limit is soft: explicitly pinned or callback-owned tracks are never
    evicted merely to satisfy it. Once those tracks are released, the next
    trim brings the cache back below its configured budget.
    """

    def __init__(self, byte_limit: int = DEFAULT_AUDIO_CACHE_BYTES) -> None:
        if int(byte_limit) <= 0:
            raise ValueError("audio cache limit must be positive")
        self.byte_limit = int(byte_limit)
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._pins: dict[str, int] = {}
        self._lock = RLock()

    @classmethod
    def from_environment(cls) -> "AudioCache":
        raw = os.environ.get(AUDIO_CACHE_MB_ENV, "").strip()
        if not raw:
            return cls()
        try:
            megabytes = int(raw)
        except ValueError:
            return cls()
        return cls(max(64, min(2048, megabytes)) * 1024 * 1024)

    def get(self, source_path: str | Path) -> DecodedAudio | None:
        key = self.key_for(source_path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.source_signature != self._source_signature(source_path):
                self._entries.pop(key, None)
                self._pins.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return entry.audio

    def put(
        self,
        source_path: str | Path,
        audio: DecodedAudio,
        *,
        protected_paths: Iterable[str | Path] = (),
    ) -> None:
        key = self.key_for(source_path)
        with self._lock:
            self._entries[key] = _CacheEntry(
                audio, self._source_signature(source_path)
            )
            self._entries.move_to_end(key)
            protected = {self.key_for(path) for path in protected_paths if path}
            protected.add(key)
            self._trim_locked(protected)

    def contains(self, source_path: str | Path) -> bool:
        return self.get(source_path) is not None

    def pin(self, source_path: str | Path) -> None:
        key = self.key_for(source_path)
        with self._lock:
            self._pins[key] = self._pins.get(key, 0) + 1

    def unpin(self, source_path: str | Path) -> None:
        key = self.key_for(source_path)
        with self._lock:
            count = self._pins.get(key, 0)
            if count <= 1:
                self._pins.pop(key, None)
            else:
                self._pins[key] = count - 1

    def remove(self, source_path: str | Path) -> bool:
        """Remove one idle entry, refusing to invalidate a pinned owner."""
        key = self.key_for(source_path)
        with self._lock:
            if self._pins.get(key, 0) > 0:
                return False
            return self._entries.pop(key, None) is not None

    def trim(self, *, protected_paths: Iterable[str | Path] = ()) -> None:
        with self._lock:
            protected = {self.key_for(path) for path in protected_paths if path}
            self._trim_locked(protected)

    def set_byte_limit(
        self,
        byte_limit: int,
        *,
        protected_paths: Iterable[str | Path] = (),
    ) -> None:
        """Change the budget and immediately evict idle excess entries."""
        byte_limit = int(byte_limit)
        if byte_limit <= 0:
            raise ValueError("audio cache limit must be positive")
        with self._lock:
            self.byte_limit = byte_limit
            _LOG.info("audio cache limit changed bytes=%d", byte_limit)
            protected = {self.key_for(path) for path in protected_paths if path}
            self._trim_locked(protected)

    def make_room(
        self,
        byte_count: int,
        *,
        protected_paths: Iterable[str | Path] = (),
    ) -> None:
        """Evict idle entries before a decode allocation begins."""
        required = max(0, int(byte_count))
        with self._lock:
            protected = {self.key_for(path) for path in protected_paths if path}
            target = max(0, self.byte_limit - required)
            self._trim_locked(protected, target=target)

    def stats(self) -> AudioCacheStats:
        with self._lock:
            return AudioCacheStats(
                entries=len(self._entries),
                bytes_used=self._bytes_used_locked(),
                byte_limit=self.byte_limit,
                pinned_entries=sum(
                    1 for key in self._entries if self._pins.get(key, 0) > 0
                ),
            )

    @staticmethod
    def key_for(source_path: str | Path) -> str:
        return os.fspath(Path(source_path).expanduser().resolve())

    def _trim_locked(self, protected: set[str], *, target: int | None = None) -> None:
        target = self.byte_limit if target is None else max(0, target)
        while self._bytes_used_locked() > target:
            victim = next(
                (
                    key
                    for key in self._entries
                    if key not in protected and self._pins.get(key, 0) == 0
                ),
                None,
            )
            if victim is None:
                break
            _LOG.info(
                "audio cache entry evicted path=%s bytes=%d",
                Path(victim).name,
                self._entries[victim].byte_size,
            )
            self._entries.pop(victim, None)

    def _bytes_used_locked(self) -> int:
        return sum(entry.byte_size for entry in self._entries.values())

    @staticmethod
    def _source_signature(source_path: str | Path) -> tuple[int, int] | None:
        try:
            stat = Path(source_path).expanduser().stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns
