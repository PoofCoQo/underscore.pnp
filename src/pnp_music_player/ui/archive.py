"""Reversible storage for inactive track metadata and waveform caches."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from pnp_music_player.models.track import TrackMetadata
from pnp_music_player.ui.persistence import MetadataStore


@dataclass(frozen=True, slots=True)
class ArchivedTrack:
    metadata: TrackMetadata
    relative_media_path: str
    media_available: bool = True


class ArchiveStore:
    """Move metadata/cache pairs without moving their source audio file."""

    def __init__(
        self,
        active_store: MetadataStore,
        archive_store: MetadataStore,
        *,
        music_directory: str | Path,
        active_waveform_directory: str | Path,
        archive_waveform_directory: str | Path,
    ) -> None:
        self.active_store = active_store
        self.archive_store = archive_store
        self.music_directory = Path(music_directory).expanduser().resolve()
        self.active_waveform_directory = Path(
            active_waveform_directory
        ).expanduser().resolve()
        self.archive_waveform_directory = Path(
            archive_waveform_directory
        ).expanduser().resolve()

    def contains(self, source_path: str | Path) -> bool:
        return self.archive_store.load(source_path) is not None

    def available_tracks(self) -> list[ArchivedTrack]:
        return [item for item in self.all_tracks() if item.media_available]

    def all_tracks(self) -> list[ArchivedTrack]:
        result: list[ArchivedTrack] = []
        for metadata in self.archive_store.load_all():
            source = Path(metadata.filepath).expanduser().resolve()
            try:
                relative = source.relative_to(self.music_directory).as_posix()
            except ValueError:
                continue
            result.append(ArchivedTrack(metadata, relative, source.is_file()))
        return sorted(
            result,
            key=lambda item: (
                item.metadata.title.casefold(),
                item.relative_media_path.casefold(),
            ),
        )

    def archive(self, metadata: TrackMetadata) -> TrackMetadata:
        source = Path(metadata.filepath).expanduser().resolve()
        try:
            source.relative_to(self.music_directory)
        except ValueError as exc:
            raise ValueError(
                "Only tracks inside the portable music directory can be archived"
            ) from exc
        active_document = self.active_store.path_for(source)
        archive_document = self.archive_store.path_for(source)
        if not active_document.is_file():
            raise FileNotFoundError("Active track metadata does not exist")
        if archive_document.exists() or self.contains(source):
            raise FileExistsError("Track is already archived")

        active_waveform = self._existing_waveform(metadata.waveform_cache_path)
        archive_waveform = self._waveform_destination(
            active_waveform, self.archive_waveform_directory
        )
        if archive_waveform is not None and archive_waveform.exists():
            raise FileExistsError(
                f"Archived waveform already exists: {archive_waveform.name}"
            )

        archived = self._copy_metadata(
            metadata,
            waveform_cache_path=(
                str(archive_waveform) if archive_waveform is not None else None
            ),
        )
        written_document = self.archive_store.save(archived)
        waveform_moved = False
        try:
            if active_waveform is not None and archive_waveform is not None:
                archive_waveform.parent.mkdir(parents=True, exist_ok=True)
                os.replace(active_waveform, archive_waveform)
                waveform_moved = True
            active_document.unlink()
        except Exception:
            if waveform_moved and archive_waveform is not None:
                active_waveform.parent.mkdir(parents=True, exist_ok=True)
                os.replace(archive_waveform, active_waveform)
            written_document.unlink(missing_ok=True)
            raise
        return archived

    def restore(self, source_path: str | Path) -> TrackMetadata:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError("Archived track's media file is missing")
        archived = self.archive_store.load(source)
        if archived is None:
            raise FileNotFoundError("Archived track configuration was not found")

        archive_document = self.archive_store.path_for(source)
        active_document = self.active_store.path_for(source)
        if active_document.exists() or self.active_store.load(source) is not None:
            raise FileExistsError("An active configuration already exists")

        archive_waveform = self._existing_waveform(archived.waveform_cache_path)
        active_waveform = self._waveform_destination(
            archive_waveform, self.active_waveform_directory
        )
        if active_waveform is not None and active_waveform.exists():
            raise FileExistsError(
                f"Active waveform already exists: {active_waveform.name}"
            )

        restored = self._copy_metadata(
            archived,
            waveform_cache_path=(
                str(active_waveform) if active_waveform is not None else None
            ),
        )
        written_document = self.active_store.save(restored)
        waveform_moved = False
        try:
            if archive_waveform is not None and active_waveform is not None:
                active_waveform.parent.mkdir(parents=True, exist_ok=True)
                os.replace(archive_waveform, active_waveform)
                waveform_moved = True
            archive_document.unlink()
        except Exception:
            if waveform_moved and active_waveform is not None:
                archive_waveform.parent.mkdir(parents=True, exist_ok=True)
                os.replace(active_waveform, archive_waveform)
            written_document.unlink(missing_ok=True)
            raise
        return restored

    @staticmethod
    def _copy_metadata(
        metadata: TrackMetadata, *, waveform_cache_path: str | None
    ) -> TrackMetadata:
        values = metadata.to_dict()
        values["waveform_cache_path"] = waveform_cache_path
        return TrackMetadata.from_dict(values)

    @staticmethod
    def _existing_waveform(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser().resolve()
        return path if path.is_file() else None

    @staticmethod
    def _waveform_destination(source: Path | None, directory: Path) -> Path | None:
        return directory / source.name if source is not None else None
