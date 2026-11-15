"""Atomic sidecar-style persistence for setup metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import MISSING, fields
from pathlib import Path
import re
import shutil
import tempfile
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from pnp_music_player.models.track import LoopRegion, TrackMetadata
from pnp_music_player.ui.metadata_paths import (
    is_foreign_absolute_path,
    relative_metadata_path,
    resolve_metadata_path,
)

_LOG = logging.getLogger("ui.metadata")


class MetadataReadError(ValueError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Could not read metadata: {path}: {reason}")


class UnsupportedMetadataVersion(MetadataReadError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError(f"duplicate JSON key: {key}")
        values[key] = value
    return values


def _check_fields(values: object, model: type, label: str) -> None:
    if not isinstance(values, dict):
        raise ValueError(f"{label} must be an object")
    hints = get_type_hints(model)
    known = {item.name: item for item in fields(model)}
    for name, item in known.items():
        if name not in values and item.default is MISSING and item.default_factory is MISSING:
            raise ValueError(f"{label}.{name} is missing")
    for name, value in values.items():
        if name not in known:
            raise ValueError(f"{label}.{name} is not supported")
        _check_type(value, hints[name], f"{label}.{name}")


def _check_type(value: object, expected: object, label: str) -> None:
    # Silence legitimately has no measurable sample peak in dBFS.
    if label in {"metadata.peak_dbfs", "metadata.sample_peak_dbfs"} and value == float("-inf"):
        return
    origin = get_origin(expected)
    if origin in (Union, UnionType):
        for option in get_args(expected):
            if _matches_type(value, option):
                _check_type(value, option, label)
                return
        raise ValueError(f"{label} has the wrong type")
    if not _matches_type(value, expected):
        raise ValueError(f"{label} has the wrong type")
    if origin is list:
        for index, item in enumerate(value):
            _check_type(item, get_args(expected)[0], f"{label}[{index}]")
    elif expected is LoopRegion:
        _check_fields(value, LoopRegion, label)


def _matches_type(value: object, expected: object) -> bool:
    origin = get_origin(expected)
    if expected is type(None):
        return value is None
    if expected is bool:
        return type(value) is bool
    if expected is int:
        return type(value) is int
    if expected is float:
        return type(value) in (int, float) and math.isfinite(value)
    if expected is str:
        return type(value) is str
    if origin is list:
        return type(value) is list
    if expected is LoopRegion:
        return type(value) is dict
    return False


def _validate_track_structure(metadata: TrackMetadata) -> None:
    # The real frame count is only available after decoding the source file.
    inferred_frames = max(
        1,
        metadata.play_end,
        *metadata.beat_positions,
        *(point + 1 for point in metadata.detected_beat_positions),
    )
    metadata.validate(inferred_frames)


class MetadataStore:
    """Store one JSON document per source path without changing audio files."""

    def __init__(
        self,
        directory: str | Path,
        *,
        music_directory: str | Path | None = None,
        waveform_cache_directory: str | Path | None = None,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.music_directory = (
            Path(music_directory).expanduser().resolve()
            if music_directory is not None else None
        )
        self.waveform_cache_directory = (
            Path(waveform_cache_directory).expanduser().resolve()
            if waveform_cache_directory is not None else None
        )
        self._documents_by_source: dict[str, Path] = {}

    def document_for(self, source_path: str | Path) -> Path:
        """Find the original document even when its audio path is foreign."""
        document = self._documents_by_source.get(str(source_path))
        return document if document is not None else self.path_for(source_path)

    def path_for(self, source_path: str | Path) -> Path:
        if is_foreign_absolute_path(str(source_path)):
            raise ValueError("Relink the missing audio file before saving its metadata")
        source = Path(source_path).expanduser().resolve()
        identity = self._source_identity(source)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        readable = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-._")
        readable = (readable or "track")[:72]
        return self.directory / f"{readable}-{digest}.json"

    def load(self, source_path: str | Path) -> TrackMetadata | None:
        source = Path(source_path).expanduser().resolve()
        expected = self.path_for(source)
        try:
            payload = self._read_payload(expected)
            stored_source = payload["metadata"]["filepath"]
            if is_foreign_absolute_path(stored_source):
                raise ValueError(
                    "metadata.filepath belongs to another platform; relink the audio file"
                )
            resolved_stored_source = resolve_metadata_path(stored_source, expected.parent)
            if self._source_identity(resolved_stored_source) != self._source_identity(source):
                raise ValueError("metadata.filepath does not match the audio file")
            metadata = self._metadata_from_payload(payload, source, expected.parent)
            self._documents_by_source[metadata.filepath] = expected
            return metadata
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, MetadataReadError):
                raise
            raise MetadataReadError(expected, str(exc)) from exc

    @staticmethod
    def _read_payload(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
            if not isinstance(payload, dict):
                raise ValueError("top-level JSON value must be an object")
            version = payload.get("format_version", 1)
            if type(version) is not int:
                raise ValueError("format_version must be an integer")
            if version != 1:
                raise UnsupportedMetadataVersion(
                    path, f"unsupported format_version {version}"
                )
            _check_fields(payload.get("metadata"), TrackMetadata, "metadata")
            if not payload["metadata"]["filepath"].strip():
                raise ValueError("metadata.filepath must not be empty")
            return payload
        except MetadataReadError:
            raise
        except FileNotFoundError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise MetadataReadError(path, str(exc)) from exc

    def _metadata_from_payload(
        self,
        payload: dict[str, object],
        source_path: str | Path,
        payload_directory: Path,
    ) -> TrackMetadata:
        values = dict(payload["metadata"])
        values["filepath"] = str(source_path)
        try:
            metadata = TrackMetadata.from_dict(values)
            _validate_track_structure(metadata)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"invalid TrackMetadata: {exc}") from exc
        cache_path = values.get("waveform_cache_path")
        if cache_path:
            if is_foreign_absolute_path(cache_path):
                metadata.waveform_cache_path = None
                return metadata
            path_value = resolve_metadata_path(cache_path, payload_directory)
            if (
                path_value.is_file()
                and self.waveform_cache_directory is not None
                and not self._is_below(path_value, self.waveform_cache_directory)
            ):
                self.waveform_cache_directory.mkdir(parents=True, exist_ok=True)
                relocated = self.waveform_cache_directory / path_value.name
                if not relocated.exists():
                    shutil.copy2(path_value, relocated)
                path_value = relocated
            elif not path_value.exists() and self.waveform_cache_directory:
                relocated = self.waveform_cache_directory / path_value.name
                if relocated.exists():
                    path_value = relocated
            metadata.waveform_cache_path = str(path_value)
        return metadata

    def save(self, metadata: TrackMetadata, *, backup_existing: bool = False) -> Path:
        values = metadata.to_dict()
        _check_fields(values, TrackMetadata, "metadata")
        if not metadata.filepath.strip():
            raise ValueError("metadata.filepath must not be empty")
        _validate_track_structure(metadata)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(metadata.filepath)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.directory,
                prefix=f".{target.stem}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                source = Path(metadata.filepath).expanduser().resolve()
                if self.music_directory is not None and self._is_below(
                    source, self.music_directory
                ):
                    values["filepath"] = relative_metadata_path(source, self.directory)
                cache_path = metadata.waveform_cache_path
                if cache_path:
                    waveform = Path(cache_path).expanduser().resolve()
                    if self.waveform_cache_directory is not None and self._is_below(
                        waveform, self.waveform_cache_directory
                    ):
                        values["waveform_cache_path"] = relative_metadata_path(
                            waveform, self.directory
                        )
                json.dump(
                    {"format_version": 1, "metadata": values},
                    stream, ensure_ascii=False, indent=2, allow_nan=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if backup_existing and target.is_file():
                with tempfile.NamedTemporaryFile(
                    "wb", dir=self.directory,
                    prefix=f".{target.name}.corrupt-", suffix=".bak",
                    delete=False,
                ) as backup:
                    backup_path = Path(backup.name)
                    with target.open("rb") as original:
                        shutil.copyfileobj(original, backup)
                    backup.flush()
                    os.fsync(backup.fileno())
                _LOG.warning("corrupt metadata backed up path=%s backup=%s", target, backup_path)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target

    def quarantine(self, source_path: str | Path) -> Path:
        """Move an unusable document aside without losing its original bytes."""
        target = self.path_for(source_path)
        with tempfile.NamedTemporaryFile(
            "wb", dir=self.directory,
            prefix=f".{target.name}.corrupt-", suffix=".bak", delete=False,
        ) as placeholder:
            backup = Path(placeholder.name)
        try:
            os.replace(target, backup)
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        _LOG.warning("corrupt metadata quarantined path=%s backup=%s", target, backup)
        return backup

    def scan_all(self) -> tuple[list[TrackMetadata], list[MetadataReadError]]:
        """Load documents and report invalid active metadata separately."""
        tracks: dict[str, TrackMetadata] = {}
        self._documents_by_source.clear()
        problems: list[MetadataReadError] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = self._read_payload(path)
                raw_source = payload["metadata"]["filepath"]
                foreign = is_foreign_absolute_path(raw_source)
                source = (
                    raw_source
                    if foreign else resolve_metadata_path(raw_source, path.parent)
                )
                if not foreign and path != self.path_for(source):
                    raise ValueError("metadata.filepath does not match the audio file")
                metadata = self._metadata_from_payload(payload, source, path.parent)
                identity = ("foreign:" + raw_source if foreign else
                            self._source_identity(Path(metadata.filepath).resolve()))
                if identity not in tracks:
                    tracks[identity] = metadata
                    self._documents_by_source[metadata.filepath] = path
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                problem = (
                    exc if isinstance(exc, MetadataReadError)
                    else MetadataReadError(path, str(exc))
                )
                problems.append(problem)
                _LOG.warning("metadata skipped path=%s reason=%s", path, problem.reason)
        return list(tracks.values()), problems

    def load_all(self) -> list[TrackMetadata]:
        return self.scan_all()[0]

    def _source_identity(self, source: Path) -> str:
        if self.music_directory is not None and self._is_below(
            source, self.music_directory
        ):
            return "music:" + source.relative_to(self.music_directory).as_posix()
        return "absolute:" + os.fspath(source)

    @staticmethod
    def _is_below(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory)
            return True
        except ValueError:
            return False
