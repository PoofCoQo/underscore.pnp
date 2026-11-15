"""Small atomic JSON persistence for Phase-6 player preferences."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import logging
import math
import os
from pathlib import Path
import tempfile

_LOG = logging.getLogger("ui.settings")

DEFAULT_KEYBINDINGS: dict[str, str] = {
    "pause_resume": "Space",
    "stop": "S",
    "go_now": "G",
    "transition_normal": "1",
    "transition_cut": "2",
    "transition_fade": "3",
    "transition_crossfade": "4",
    "mute_local": "M",
    "mute_discord": "Shift+M",
    "fullscreen": "F11",
    "favorite_1": "Shift+1",
    "favorite_2": "Shift+2",
    "favorite_3": "Shift+3",
    "favorite_4": "Shift+4",
    "favorite_5": "Shift+5",
    "favorite_6": "Shift+6",
    "favorite_7": "Shift+7",
    "favorite_8": "Shift+8",
    "favorite_9": "Shift+9",
    "favorite_10": "Shift+0",
}


def default_keybindings() -> dict[str, str]:
    return dict(DEFAULT_KEYBINDINGS)


@dataclass(slots=True)
class PlayerSettings:
    last_category: str = "All"
    last_album: str = "All Albums"
    master_db: float = -12.0
    discord_db: float = -12.0
    selected_output: str = "default"
    transition_duration: float = 2.5
    transition_mode: str = "normal"
    fullscreen: bool = False
    audio_cache_mb: int | None = None
    keybindings: dict[str, str] = field(default_factory=default_keybindings)

    def clamp(self) -> list[str]:
        """Repair malformed fields individually, then limit numeric values."""
        defaults = PlayerSettings()
        repaired: list[str] = []
        for item in fields(self):
            name = item.name
            value = getattr(self, name)
            default = getattr(defaults, name)
            if name == "keybindings":
                continue
            if name in {"master_db", "discord_db", "transition_duration"}:
                try:
                    valid = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    valid = False
            elif name == "audio_cache_mb":
                valid = value is None or type(value) is int
            else:
                valid = type(value) is type(default)
            if not valid:
                setattr(self, name, default)
                repaired.append(name)
        for name, lower, upper in (
            ("master_db", -80.0, 0.0),
            ("discord_db", -80.0, 0.0),
            ("transition_duration", 0.5, 10.0),
        ):
            before = getattr(self, name)
            after = min(upper, max(lower, float(before)))
            setattr(self, name, after)
            if before != after and name not in repaired:
                repaired.append(name)
        if self.transition_mode not in {"normal", "hard_cut", "fade", "crossfade"}:
            self.transition_mode = "normal"
            repaired.append("transition_mode")
        if self.audio_cache_mb is not None:
            before = self.audio_cache_mb
            self.audio_cache_mb = max(64, min(2048, int(before)))
            if before != self.audio_cache_mb and "audio_cache_mb" not in repaired:
                repaired.append("audio_cache_mb")
        incoming = self.keybindings if isinstance(self.keybindings, dict) else {}
        if not isinstance(self.keybindings, dict):
            repaired.append("keybindings")
        for action, value in incoming.items():
            if action in DEFAULT_KEYBINDINGS and not isinstance(value, str):
                repaired.append(f"keybindings.{action}")
        self.keybindings = {
            action: (incoming.get(action, default).strip()
                     if isinstance(incoming.get(action, default), str) else default)
            for action, default in DEFAULT_KEYBINDINGS.items()
        }
        return repaired


class PlayerSettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.recovery_message = ""
        self.backup_path: Path | None = None
        self._save_blocked = False

    def load(self) -> PlayerSettings:
        self.recovery_message = ""
        self.backup_path = None
        self._save_blocked = False
        original: bytes | None = None
        reason = ""
        try:
            original = self.path.read_bytes()
            values = json.loads(original.decode("utf-8"))
            if not isinstance(values, dict):
                raise ValueError("top-level settings must be an object")
            known = {item.name for item in fields(PlayerSettings)}
            values = {name: value for name, value in values.items() if name in known}
            settings = PlayerSettings(**values)
        except FileNotFoundError:
            return PlayerSettings()
        except OSError as exc:
            self._save_blocked = True
            self.recovery_message = (
                f"Settings could not be read. Using defaults; saving is disabled "
                f"to protect the original file: {exc}"
            )
            _LOG.warning(self.recovery_message)
            return PlayerSettings()
        except (ValueError, TypeError) as exc:
            reason = str(exc)
            settings = PlayerSettings()
        repaired = settings.clamp()
        if reason or repaired:
            reason = reason or "invalid fields: " + ", ".join(repaired)
            try:
                with tempfile.NamedTemporaryFile(
                    "wb", dir=self.path.parent,
                    prefix=f"{self.path.name}.corrupt-", suffix=".bak", delete=False,
                ) as backup:
                    backup.write(original)
                    backup.flush()
                    os.fsync(backup.fileno())
                    self.backup_path = Path(backup.name)
                self.recovery_message = (
                    "Some settings were invalid and have been restored to defaults. "
                    f"The original file was backed up to {self.backup_path}."
                )
            except OSError as exc:
                self._save_blocked = True
                self.recovery_message = (
                    "Some settings were invalid and have been restored to defaults. "
                    "The backup could not be created; saving is disabled to protect "
                    f"the original file: {exc}"
                )
            _LOG.warning("%s Reason: %s", self.recovery_message, reason)
        return settings

    def save(self, settings: PlayerSettings) -> None:
        if self._save_blocked:
            return
        settings.clamp()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(settings), stream, ensure_ascii=False, indent=2,
                          allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
