"""Streaming identity hashes for unchanged source media files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event
from typing import Callable

HashProgress = Callable[[float], None]
HASH_CHUNK_SIZE = 1024 * 1024


def calculate_source_sha256(
    path: str | Path,
    *,
    progress_callback: HashProgress | None = None,
    cancel_event: Event | None = None,
) -> str:
    """Return a lowercase SHA-256 digest without loading the file into RAM."""
    source = Path(path).expanduser().resolve()
    total = source.stat().st_size
    processed = 0
    digest = hashlib.sha256()
    if progress_callback is not None:
        progress_callback(0.0)
    with source.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("source hash calculation cancelled")
            digest.update(chunk)
            processed += len(chunk)
            if progress_callback is not None:
                progress_callback(1.0 if total == 0 else processed / total)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("source hash calculation cancelled")
    if progress_callback is not None:
        progress_callback(1.0)
    return digest.hexdigest()
