"""Portable on-disk layout used by the desktop application."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

DATA_DIRECTORY_ENV = "PNP_MUSIC_DATA_DIR"


@dataclass(frozen=True, slots=True)
class DataPaths:
    root: Path
    music: Path
    metadata: Path
    waveform_cache: Path
    archive_metadata: Path
    archive_waveform_cache: Path

    @classmethod
    def below(cls, root: str | Path) -> "DataPaths":
        base = Path(root).expanduser().resolve()
        return cls(
            root=base,
            music=base / "music",
            metadata=base / "metadata",
            waveform_cache=base / "cache" / "waveforms",
            archive_metadata=base / "archive" / "metadata",
            archive_waveform_cache=base / "archive" / "cache" / "waveforms",
        )

    def ensure(self) -> None:
        for path in (
            self.music,
            self.metadata,
            self.waveform_cache,
            self.archive_metadata,
            self.archive_waveform_cache,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_data_paths() -> DataPaths:
    """Resolve an override or a ``data`` directory beside the project/app."""
    override = os.environ.get(DATA_DIRECTORY_ENV)
    if override:
        return DataPaths.below(override)

    return DataPaths.below(resolve_program_root() / "data")


def resolve_program_root() -> Path:
    """Find the visible project or portable launcher directory."""

    # Editable/source installation: keep data at the repository root rather
    # than below src/pnp_music_player or the virtual environment.
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pyproject.toml").is_file():
            return parent

    # A macOS app's executable lives inside the .app bundle; exports belong
    # beside that bundle, never inside its signed Contents directory.
    launcher = Path(sys.argv[0]).expanduser().resolve()
    for parent in launcher.parents:
        if parent.suffix == ".app":
            return parent.parent
    return launcher.parent
