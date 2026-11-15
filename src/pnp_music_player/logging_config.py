"""Non-blocking, bounded and platform-neutral application logging."""

from __future__ import annotations

import faulthandler
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
import os
import queue
import re
import sys
import threading
from types import TracebackType
from typing import TextIO
from uuid import uuid4

from platformdirs import PlatformDirs

from pnp_music_player.branding import PRODUCT_NAME

APP_LOG_NAME = "underscore.pnp.log"
CRASH_LOG_NAME = "underscore.pnp-crash.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
LOG_DIRECTORY_ENV = "PNP_MUSIC_LOG_DIR"

_RUNTIME: LoggingRuntime | None = None
_REGISTERED_SECRETS: set[str] = set()
_SECRET_PATTERNS = (
    re.compile(r"(?i)(PNP_MUSIC_DISCORD_TOKEN\s*=\s*)([^\s]+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bot\s+|bearer\s+)?)([^\s]+)"),
    re.compile(r"(?i)(\btoken\s*[:=]\s*)([^\s]+)"),
)


def default_log_directory() -> Path:
    """Return an OS-native, per-user log directory."""
    override = os.environ.get(LOG_DIRECTORY_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(
        PlatformDirs(
            appname=PRODUCT_NAME, appauthor=False, roaming=False
        ).user_log_path
    )


def redact_secrets(value: object) -> str:
    text = str(value)
    for secret in tuple(_REGISTERED_SECRETS):
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1<redacted>", text)
    return text


def register_secret(value: str) -> None:
    """Register an in-process credential that must never reach a handler."""
    secret = str(value).strip()
    if secret:
        _REGISTERED_SECRETS.add(secret)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        return True


class IsoFormatter(logging.Formatter):
    """Human-readable format with an aware ISO timestamp."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="milliseconds"
        )

    def formatException(
        self,
        ei: tuple[type[BaseException], BaseException, TracebackType | None]
        | tuple[None, None, None],
    ) -> str:
        return redact_secrets(super().formatException(ei))


class SessionFilter(logging.Filter):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self.session_id
        return True


class LoggingRuntime:
    """Own handlers, hooks and the single background log writer."""

    def __init__(
        self,
        *,
        log_directory: Path,
        session_id: str,
        listener: QueueListener,
        queue_handler: QueueHandler,
        output_handlers: tuple[logging.Handler, ...],
        old_root_handlers: tuple[logging.Handler, ...],
        old_root_level: int,
        crash_stream: TextIO | None,
        faulthandler_owned: bool,
        old_excepthook: object,
        old_threading_excepthook: object,
        old_unraisablehook: object,
        old_qt_handler: object | None,
        qt_handler_installed: bool,
        file_logging_available: bool,
    ) -> None:
        self.log_directory = log_directory
        self.session_id = session_id
        self.listener = listener
        self.queue_handler = queue_handler
        self.output_handlers = output_handlers
        self.old_root_handlers = old_root_handlers
        self.old_root_level = old_root_level
        self.crash_stream = crash_stream
        self.faulthandler_owned = faulthandler_owned
        self.old_excepthook = old_excepthook
        self.old_threading_excepthook = old_threading_excepthook
        self.old_unraisablehook = old_unraisablehook
        self.old_qt_handler = old_qt_handler
        self.qt_handler_installed = qt_handler_installed
        self.file_logging_available = file_logging_available
        self.diagnostic = False
        self.closed = False

    @property
    def log_file(self) -> Path:
        return self.log_directory / APP_LOG_NAME

    def set_diagnostic(self, enabled: bool) -> None:
        self.diagnostic = bool(enabled)
        level = logging.DEBUG if self.diagnostic else logging.INFO
        for handler in self.output_handlers:
            handler.setLevel(level)
        # HTTP debug output is both noisy and the most likely third-party
        # source of credential-bearing headers. Never enable it implicitly.
        logging.getLogger("discord.http").setLevel(logging.WARNING)
        logging.getLogger("discord.gateway").setLevel(
            logging.DEBUG if self.diagnostic else logging.INFO
        )
        logging.getLogger("app.logging").info(
            "diagnostic logging %s", "enabled" if self.diagnostic else "disabled"
        )

    def close(self) -> None:
        global _RUNTIME
        if self.closed:
            return
        self.closed = True
        logging.getLogger("app.lifecycle").info("logging shutdown requested")
        self.listener.stop()

        root = logging.getLogger()
        if self.queue_handler in root.handlers:
            root.removeHandler(self.queue_handler)
        for handler in self.output_handlers:
            handler.close()
        for handler in self.old_root_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(self.old_root_level)

        sys.excepthook = self.old_excepthook  # type: ignore[assignment]
        threading.excepthook = self.old_threading_excepthook  # type: ignore[assignment]
        sys.unraisablehook = self.old_unraisablehook  # type: ignore[assignment]
        if self.qt_handler_installed:
            try:
                from PySide6.QtCore import qInstallMessageHandler

                qInstallMessageHandler(self.old_qt_handler)
            except Exception:
                pass
        if self.faulthandler_owned and faulthandler.is_enabled():
            faulthandler.disable()
        if self.crash_stream is not None:
            self.crash_stream.close()
        if _RUNTIME is self:
            _RUNTIME = None


def current_runtime() -> LoggingRuntime | None:
    return _RUNTIME


def current_log_directory() -> Path:
    runtime = current_runtime()
    return runtime.log_directory if runtime is not None else default_log_directory()


def diagnostic_logging_enabled() -> bool:
    runtime = current_runtime()
    return bool(runtime is not None and runtime.diagnostic)


def set_diagnostic_logging(enabled: bool) -> bool:
    runtime = current_runtime()
    if runtime is None:
        return False
    runtime.set_diagnostic(enabled)
    return runtime.diagnostic


def configure_logging(
    *,
    log_directory: str | Path | None = None,
    console: bool = True,
    install_hooks: bool = True,
    install_qt_handler: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> LoggingRuntime:
    """Configure the root logger once and return its owned lifecycle."""
    global _RUNTIME
    if _RUNTIME is not None and not _RUNTIME.closed:
        return _RUNTIME

    directory = Path(log_directory) if log_directory is not None else default_log_directory()
    session_id = uuid4().hex[:12]
    formatter = IsoFormatter(
        "%(asctime)s %(levelname)-8s [%(threadName)s] "
        "%(name)s session=%(session_id)s: %(message)s"
    )
    session_filter = SessionFilter(session_id)
    secret_filter = SecretFilter()
    output_handlers: list[logging.Handler] = []
    file_available = False
    setup_failure: Exception | None = None

    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / APP_LOG_NAME,
            maxBytes=max(1, int(max_bytes)),
            backupCount=max(0, int(backup_count)),
            encoding="utf-8",
            delay=True,
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(session_filter)
        file_handler.addFilter(secret_filter)
        output_handlers.append(file_handler)
        file_available = True
    except Exception as exc:
        setup_failure = exc

    if console and sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(session_filter)
        console_handler.addFilter(secret_filter)
        output_handlers.append(console_handler)
    if not output_handlers:
        output_handlers.append(logging.NullHandler())

    log_queue: queue.SimpleQueue[logging.LogRecord | None] = queue.SimpleQueue()
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)
    listener = QueueListener(
        log_queue, *output_handlers, respect_handler_level=True
    )

    root = logging.getLogger()
    old_handlers = tuple(root.handlers)
    old_level = root.level
    for handler in old_handlers:
        root.removeHandler(handler)
    root.addHandler(queue_handler)
    root.setLevel(logging.DEBUG)
    listener.start()

    old_excepthook = sys.excepthook
    old_thread_hook = threading.excepthook
    old_unraisablehook = sys.unraisablehook
    if install_hooks:
        sys.excepthook = _handle_unhandled_exception
        threading.excepthook = _handle_thread_exception
        sys.unraisablehook = _handle_unraisable

    crash_stream: TextIO | None = None
    faulthandler_owned = False
    if file_available and not faulthandler.is_enabled():
        try:
            crash_stream = (directory / CRASH_LOG_NAME).open("w", encoding="utf-8")
            faulthandler.enable(file=crash_stream, all_threads=True)
            faulthandler_owned = True
        except Exception:
            if crash_stream is not None:
                crash_stream.close()
            crash_stream = None

    old_qt_handler: object | None = None
    qt_installed = False
    if install_qt_handler:
        try:
            from PySide6.QtCore import qInstallMessageHandler

            old_qt_handler = qInstallMessageHandler(_handle_qt_message)
            qt_installed = True
        except Exception:
            logging.getLogger("app.logging").exception(
                "could not install the Qt message handler"
            )

    runtime = LoggingRuntime(
        log_directory=directory,
        session_id=session_id,
        listener=listener,
        queue_handler=queue_handler,
        output_handlers=tuple(output_handlers),
        old_root_handlers=old_handlers,
        old_root_level=old_level,
        crash_stream=crash_stream,
        faulthandler_owned=faulthandler_owned,
        old_excepthook=old_excepthook,
        old_threading_excepthook=old_thread_hook,
        old_unraisablehook=old_unraisablehook,
        old_qt_handler=old_qt_handler,
        qt_handler_installed=qt_installed,
        file_logging_available=file_available,
    )
    _RUNTIME = runtime
    try:
        from pnp_music_player.config_env import load_discord_token

        register_secret(load_discord_token())
    except Exception:
        # Credential discovery must never be able to break application start.
        pass
    if setup_failure is not None:
        logging.getLogger("app.logging").warning(
            "file logging unavailable; using console fallback: %s", setup_failure
        )
    logging.getLogger("app.logging").info(
        "logging initialized directory=%s file_available=%s",
        directory,
        file_available,
    )
    return runtime


def _handle_unhandled_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> None:
    if issubclass(exception_type, KeyboardInterrupt):
        sys.__excepthook__(exception_type, exception, traceback)
        return
    logging.getLogger("app.crash").critical(
        "unhandled exception in main thread",
        exc_info=(exception_type, exception, traceback),
    )


def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    logging.getLogger("app.crash").error(
        "unhandled exception in thread %s",
        args.thread.name if args.thread is not None else "unknown",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _handle_unraisable(args: object) -> None:
    exception = getattr(args, "exc_value", None)
    logger = logging.getLogger("app.crash")
    details = (
        "unraisable exception in %r: %s",
        getattr(args, "object", None),
        getattr(args, "err_msg", None) or exception,
    )
    if exception is None:
        logger.error(*details)
        return
    logger.error(
        *details,
        exc_info=(type(exception), exception, getattr(args, "exc_traceback", None)),
    )


def _handle_qt_message(mode: object, context: object, message: str) -> None:
    try:
        from PySide6.QtCore import QtMsgType

        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        level = levels.get(mode, logging.WARNING)
    except Exception:
        level = logging.WARNING
    file_name = getattr(context, "file", None)
    line = getattr(context, "line", 0)
    location = f" ({file_name}:{line})" if file_name else ""
    logging.getLogger("qt.qml").log(level, "%s%s", message, location)
