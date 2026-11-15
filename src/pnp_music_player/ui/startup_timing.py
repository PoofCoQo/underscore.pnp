"""Measure launcher-to-first-frame latency without delaying application startup."""

from __future__ import annotations

import logging
import os
import signal
from time import monotonic_ns, time_ns

from PySide6.QtCore import QObject, Slot

_LOG = logging.getLogger("app.lifecycle")
LAUNCH_EPOCH_NS_ENV = "PNP_MUSIC_LAUNCH_EPOCH_NS"
_MAX_LAUNCH_TO_ENTRY_NS = 60 * 60 * 1_000_000_000
_SPLASH_PID_ENV = "PNP_MUSIC_SPLASH_PID"
_LAUNCHER_PID_ENV = "PNP_MUSIC_LAUNCHER_PID"
_SPLASH_DIR_ENV = "PNP_MUSIC_SPLASH_DIR"


def _dismiss_launcher_splash() -> None:
    """Dismiss the launcher's splash when the main window queues its first frame."""
    splash_dir = os.environ.get(_SPLASH_DIR_ENV)
    temp_root = os.environ.get("TEMP")
    if splash_dir and temp_root:
        normalized = os.path.normcase(os.path.realpath(splash_dir))
        temp_directory = os.path.normcase(os.path.realpath(temp_root))
        if (
            os.path.dirname(normalized) == temp_directory
            and os.path.basename(normalized).startswith("underscore-pnp-splash-")
        ):
            try:
                os.rmdir(splash_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                _LOG.warning("launcher splash could not be dismissed: %s", exc)

    # Linux start.sh owns the sibling process; the direct-parent check prevents
    # a normal Python launch from signaling an inherited or unrelated PID.
    try:
        launcher_pid = int(os.environ[_LAUNCHER_PID_ENV])
        splash_pid = int(os.environ[_SPLASH_PID_ENV])
    except (KeyError, ValueError):
        return
    if (
        launcher_pid != os.getppid()
        or splash_pid <= 0
        or splash_pid in (launcher_pid, os.getpid())
    ):
        return
    try:
        os.kill(splash_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        _LOG.warning("launcher splash could not be dismissed: %s", exc)


class StartupTiming(QObject):
    """Log the first queued Qt Quick frame exactly once per application run."""

    def __init__(
        self,
        *,
        entry_monotonic_ns: int | None = None,
        entry_epoch_ns: int | None = None,
    ) -> None:
        super().__init__()
        self.entry_monotonic_ns = (
            monotonic_ns() if entry_monotonic_ns is None else entry_monotonic_ns
        )
        self.entry_epoch_ns = time_ns() if entry_epoch_ns is None else entry_epoch_ns
        self.launch_to_entry_ns = self._launch_to_entry_ns(
            os.environ.get(LAUNCH_EPOCH_NS_ENV, "")
        )
        self.track_count = 0
        self.recorded = False

    def _launch_to_entry_ns(self, raw: str) -> int | None:
        try:
            elapsed = self.entry_epoch_ns - int(raw)
        except ValueError:
            return None
        if 0 <= elapsed <= _MAX_LAUNCH_TO_ENTRY_NS:
            return elapsed
        return None

    @Slot()
    def record_first_frame(self) -> None:
        if self.recorded:
            return
        self.recorded = True
        _dismiss_launcher_splash()
        entry_to_frame_ns = max(0, monotonic_ns() - self.entry_monotonic_ns)
        launcher_to_frame = (
            f"{(self.launch_to_entry_ns + entry_to_frame_ns) / 1_000_000:.1f}"
            if self.launch_to_entry_ns is not None else "unavailable"
        )
        launcher_to_entry = (
            f"{self.launch_to_entry_ns / 1_000_000:.1f}"
            if self.launch_to_entry_ns is not None else "unavailable"
        )
        _LOG.info(
            "startup first frame queued launcher_to_first_frame_ms=%s "
            "launcher_to_app_entry_ms=%s app_entry_to_first_frame_ms=%.1f "
            "library_tracks=%d",
            launcher_to_frame,
            launcher_to_entry,
            entry_to_frame_ns / 1_000_000,
            self.track_count,
        )
