"""Small, non-executing loader for device-local application secrets."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat
import sys
import tempfile

from pnp_music_player.branding import PRODUCT_NAME

DISCORD_TOKEN_ENV = "PNP_MUSIC_DISCORD_TOKEN"
ENV_FILE_OVERRIDE = "PNP_MUSIC_ENV_FILE"
_LOG = logging.getLogger("config.env")


def default_env_file() -> Path:
    """Return the per-user secret file path for the current platform."""
    override = os.environ.get(ENV_FILE_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    return _platform_env_file()


def _platform_env_file() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / PRODUCT_NAME / ".env"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / PRODUCT_NAME / ".env"
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / PRODUCT_NAME / ".env"


def load_discord_token() -> str:
    """Load the token from the process environment or the protected .env.

    Only one known key is parsed. The file is never sourced or executed.
    A non-empty process variable deliberately takes precedence.
    """
    direct = os.environ.get(DISCORD_TOKEN_ENV, "").strip()
    if direct:
        return direct
    _, value = _read_env_entry(default_env_file(), DISCORD_TOKEN_ENV)
    return value


def save_discord_token(token: str) -> Path:
    """Atomically store the Discord token in the protected per-user .env."""
    token = str(token).strip()
    if "\n" in token or "\r" in token:
        raise ValueError("Discord token must be a single line")
    path = default_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(f"{DISCORD_TOKEN_ENV}={token}\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            path.chmod(0o600)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_env_entry(path: Path, key: str) -> tuple[bool, str]:
    try:
        file_stat = path.stat()
        if sys.platform != "win32" and stat.S_IMODE(file_stat.st_mode) & 0o077:
            _LOG.error(
                "refusing insecure secret file %s; set its permissions to 0600",
                path,
            )
            return True, ""
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False, ""
    except OSError as exc:
        _LOG.warning("cannot read secret file %s: %s", path, exc)
        return True, ""

    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        name, separator, raw_value = candidate.partition("=")
        if not separator or name.strip() != key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return True, value.strip()
    return False, ""
