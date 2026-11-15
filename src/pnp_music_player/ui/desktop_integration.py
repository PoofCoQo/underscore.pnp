"""Manage the player launcher under the user's Linux applications directory."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from pnp_music_player.branding import PRODUCT_DESKTOP_ID, PRODUCT_NAME
from pnp_music_player.ui.app_icon import APP_ICON_PATH


_MANAGED_MARKER = "X-UnderscorePnP-Managed=true"


def _quote_exec_path(path: Path) -> str:
    """Quote an executable path using Desktop Entry Exec field rules."""
    value = str(path)
    value = value.replace("\\", "\\\\\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("`", "\\`")
    value = value.replace("$", "\\\\$")
    value = value.replace("%", "%%")
    return f'"{value}"'


def _desktop_entry_path(data_home: Path | None = None) -> Path:
    root = data_home or Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    )
    return root / "applications" / f"{PRODUCT_DESKTOP_ID}.desktop"


def install_linux_desktop_entry(
    project_dir: Path, *, data_home: Path | None = None
) -> Path:
    """Register a desktop entry matching Qt's Wayland desktop file name."""
    launcher = project_dir.resolve() / "start.sh"
    if not launcher.is_file():
        raise FileNotFoundError(f"player launcher not found: {launcher}")
    if not APP_ICON_PATH.is_file():
        raise FileNotFoundError(f"application icon not found: {APP_ICON_PATH}")
    entry = _desktop_entry_path(data_home)
    if entry.is_symlink() or (
        entry.exists() and _MANAGED_MARKER not in entry.read_text(encoding="utf-8")
    ):
        raise FileExistsError(f"refusing to overwrite an unmanaged launcher: {entry}")
    content = "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            f"Name={PRODUCT_NAME}",
            "Comment=Music player for tabletop role-playing sessions",
            f"Exec={_quote_exec_path(launcher)}",
            f"Icon={APP_ICON_PATH}",
            "Terminal=false",
            "Categories=AudioVideo;Audio;",
            _MANAGED_MARKER,
            "",
        )
    )
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(content, encoding="utf-8")
    return entry


def remove_linux_desktop_entry(*, data_home: Path | None = None) -> Path | None:
    """Remove only a regular launcher file explicitly managed by this app."""
    entry = _desktop_entry_path(data_home)
    if entry.is_symlink():
        raise FileExistsError(f"refusing to remove a symlinked launcher: {entry}")
    if not entry.exists():
        return None
    if not entry.is_file():
        raise FileExistsError(f"refusing to remove an unmanaged launcher: {entry}")
    try:
        lines = entry.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise FileExistsError(f"refusing to remove an unreadable launcher: {entry}") from exc
    if _MANAGED_MARKER not in lines:
        raise FileExistsError(f"refusing to remove an unmanaged launcher: {entry}")
    entry.unlink()
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Linux player launcher")
    parser.add_argument("project_dir", type=Path, nargs="?")
    parser.add_argument("--remove", action="store_true", help="Remove the managed launcher")
    args = parser.parse_args()
    try:
        if args.remove:
            if args.project_dir is not None:
                parser.error("project_dir is not used with --remove")
            entry = remove_linux_desktop_entry()
            if entry is None:
                print("No managed player launcher is installed.")
            else:
                print(f"Player launcher removed: {entry}")
            return 0
        if args.project_dir is None:
            parser.error("project_dir is required unless --remove is specified")
        entry = install_linux_desktop_entry(args.project_dir)
    except OSError as exc:
        print(f"Could not update player launcher: {exc}", file=sys.stderr)
        return 1
    print(f"Player launcher installed: {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
