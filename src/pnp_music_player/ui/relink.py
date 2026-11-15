"""Safely bind an existing metadata document to a selected audio file."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile

from pnp_music_player.models.track import TrackMetadata
from pnp_music_player.ui.persistence import MetadataStore, _validate_track_structure


@dataclass(frozen=True)
class RelinkRequest:
    document: Path
    source: Path
    metadata: TrackMetadata
    archived: bool
    replace_existing: bool = False


class MetadataRelinker:
    def __init__(self, active: MetadataStore, archived: MetadataStore) -> None:
        self.active = active
        self.archived = archived

    def prepare(
        self, document: str | Path, source: str | Path, *, replace_existing: bool = False
    ) -> RelinkRequest:
        path = Path(document).expanduser().resolve()
        audio = Path(source).expanduser().resolve()
        if path.suffix.lower() != ".json" or not path.is_file():
            raise ValueError("Select an existing metadata JSON file")
        if not audio.is_file():
            raise FileNotFoundError(f"Audio file is missing: {audio}")
        target = self.active.path_for(audio)
        if target.exists() and target != path and not replace_existing:
            raise FileExistsError(f"Audio file already has metadata: {target}")
        archive_target = self.archived.path_for(audio)
        if archive_target.exists() and archive_target != path:
            raise FileExistsError("Audio file already has an archived configuration")
        payload = MetadataStore._read_payload(path)
        values = dict(payload["metadata"])
        values["filepath"] = str(audio)
        # The old cache belongs to the old source path/stat, even when the
        # audio bytes are identical. Analysis will create a valid new cache.
        values["waveform_cache_path"] = None
        metadata = TrackMetadata.from_dict(values)
        _validate_track_structure(metadata)
        archived = path.parent == self.archived.directory
        return RelinkRequest(path, audio, metadata, archived, replace_existing)

    def commit(self, request: RelinkRequest, metadata: TrackMetadata) -> Path:
        """Write the new active document before removing an old local one."""
        if Path(metadata.filepath).resolve() != request.source:
            raise ValueError("Relink source changed during analysis")
        target = self.active.path_for(request.source)
        old_is_local = request.document.parent in (
            self.active.directory, self.archived.directory
        )
        if target.exists() and target != request.document and not request.replace_existing:
            raise FileExistsError(f"Audio file already has metadata: {target}")
        archive_target = self.archived.path_for(request.source)
        if archive_target.exists() and archive_target != request.document:
            raise FileExistsError("Audio file already has an archived configuration")
        if target.exists() and request.replace_existing:
            with tempfile.NamedTemporaryFile(
                "wb", dir=self.active.directory,
                prefix=f".{target.name}.replaced-", suffix=".bak", delete=False,
            ) as backup:
                with target.open("rb") as existing:
                    shutil.copyfileobj(existing, backup)
                backup.flush()
                os.fsync(backup.fileno())
        written = self.active.save(metadata)
        if old_is_local and request.document != written:
            try:
                request.document.unlink()
            except OSError:
                written.unlink(missing_ok=True)
                raise
        return written
