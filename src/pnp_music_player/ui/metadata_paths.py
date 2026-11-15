"""Portable relative paths and explicit detection of foreign absolute paths."""

import os
from pathlib import Path, PureWindowsPath


def is_foreign_absolute_path(value: str) -> bool:
    windows = PureWindowsPath(value)
    if os.name == "nt":
        return value.startswith("/") and not windows.drive
    # Drive-relative Windows paths also cannot be resolved on POSIX.
    return bool(windows.drive)


def resolve_metadata_path(value: str, directory: Path) -> Path:
    if is_foreign_absolute_path(value):
        raise ValueError(f"Audio path belongs to another platform; relink it: {value}")
    path = Path(value)
    if path.is_absolute():
        return path
    literal = (directory / path).resolve()
    # Keep an existing POSIX filename containing a literal backslash intact.
    if "\\" in value and not literal.exists():
        return (directory / value.replace("\\", "/")).resolve()
    return literal


def relative_metadata_path(path: Path, directory: Path) -> str:
    return Path(os.path.relpath(path, directory)).as_posix()


def metadata_filename(value: str) -> str:
    if is_foreign_absolute_path(value):
        return PureWindowsPath(value).name
    return Path(value).name
