"""Portable, audio-free metadata exports and import previews."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from pnp_music_player.models.track import TrackMetadata
from pnp_music_player.ui.metadata_paths import metadata_filename
from pnp_music_player.ui.persistence import MetadataStore


def _safe_component(value: str, fallback: str) -> str:
    # Windows rejects these characters; the same names must work on all hosts.
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned.upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        return fallback
    cleaned = cleaned[:100].rstrip(" .")
    while len(cleaned.encode("utf-8")) > 100:
        cleaned = cleaned[:-1]
    return cleaned.rstrip(" .") or fallback


def export_filename(metadata: TrackMetadata) -> str:
    artist = _safe_component(metadata.artist, "Unknown Artist")
    title = _safe_component(metadata.title, "Untitled Track")
    return f"{artist} - {title}.json"


def portable_payload(metadata: TrackMetadata) -> dict[str, object]:
    if not metadata.source_sha256:
        raise ValueError(f"{metadata.title}: open this track to add its audio hash before exporting")
    values = metadata.to_dict()
    values["filepath"] = metadata_filename(metadata.filepath)
    values["waveform_cache_path"] = None
    return {"format_version": 1, "metadata": values}


def export_plan(metadata: list[TrackMetadata], directory: Path) -> list[dict[str, object]]:
    """Give every selected track one distinct, readable target filename."""
    seen: set[str] = set()
    plan: list[dict[str, object]] = []
    for track in sorted(metadata, key=lambda item: (
        item.artist.casefold(), item.title.casefold(), item.filepath.casefold()
    )):
        portable_payload(track)
        base = export_filename(track)[:-5]
        filename = f"{base}.json"
        number = 2
        while filename.casefold() in seen:
            filename = f"{base} ({number}).json"
            number += 1
        seen.add(filename.casefold())
        target = directory / filename
        plan.append({
            "source": track.filepath,
            "artist": track.artist,
            "title": track.title,
            "fileName": filename,
            "exists": target.exists(),
        })
    return plan


def write_exports(
    metadata: list[TrackMetadata], directory: Path, decisions: dict[str, str]
) -> tuple[int, int]:
    """Write only JSON files; existing targets require an explicit replace choice."""
    plan = export_plan(metadata, directory)
    by_source = {item.filepath: item for item in metadata}
    directory.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for item in plan:
        target = directory / str(item["fileName"])
        decision = decisions.get(str(item["source"]), "skip")
        if target.exists() and decision != "replace":
            skipped += 1
            continue
        payload = portable_payload(by_source[str(item["source"])])
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory,
                prefix=".metadata-export-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if decision == "replace":
                os.replace(temporary, target)
                temporary = None
            else:
                # Exclusive creation also protects a file that appears after
                # the preview, including on a shared export directory.
                try:
                    with target.open("xb") as output, temporary.open("rb") as source:
                        try:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        except Exception:
                            target.unlink(missing_ok=True)
                            raise
                except FileExistsError:
                    skipped += 1
                    continue
                temporary.unlink()
                temporary = None
            written += 1
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return written, skipped


def inspect_import(path: Path, music_directory: Path) -> dict[str, object]:
    """Read one existing or exported JSON and suggest an unambiguous audio file."""
    payload = MetadataStore._read_payload(path)
    values = payload["metadata"]
    media_name = metadata_filename(str(values["filepath"]))
    matches = sorted(
        candidate for candidate in music_directory.rglob("*")
        if candidate.is_file() and candidate.name.casefold() == media_name.casefold()
    ) if music_directory.is_dir() else []
    return {
        "document": str(path),
        "artist": str(values["artist"]),
        "title": str(values["title"]),
        "mediaName": media_name,
        "hash": str(values.get("source_sha256") or ""),
        "suggestedAudio": str(matches[0]) if len(matches) == 1 else "",
        "matchCount": len(matches),
    }
