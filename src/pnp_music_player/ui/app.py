"""Application entry point for the desktop UI."""

from __future__ import annotations

import logging
from pathlib import Path
import platform
import sys
from time import monotonic_ns, time_ns

from PySide6.QtCore import QCoreApplication, QEvent, QUrl, qVersion
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickWindow

from pnp_music_player import __version__
from pnp_music_player.branding import (
    PRODUCT_DESKTOP_ID,
    PRODUCT_NAME,
    PRODUCT_ORGANIZATION,
)
from pnp_music_player.ui.app_icon import APP_ICON_PATH
from pnp_music_player.ui.bridge import SetupBridge
from pnp_music_player.ui.audio_runtime import AudioRuntime
from pnp_music_player.ui.persistence import MetadataStore
from pnp_music_player.ui.player_bridge import PlayerBridge
from pnp_music_player.ui.storage import DataPaths, resolve_data_paths, resolve_program_root
from pnp_music_player.ui.startup_timing import StartupTiming
from pnp_music_player.ui.shutdown import ShutdownController
from pnp_music_player.ui.waveform_item import WaveformItem
from pnp_music_player.logging_config import configure_logging

_LOG = logging.getLogger("app.lifecycle")


def _configure_quick_controls_style(platform_name: str) -> None:
    """Avoid native Windows controls, which reject our custom QML visuals."""
    if platform_name != "win32":
        return
    from PySide6.QtQuickControls2 import QQuickStyle

    if QQuickStyle.name().lower() == "windows":
        QQuickStyle.setStyle("Fusion")
        _LOG.info("Qt Quick Controls style selected: Fusion")


def _configure_app_icon(app: QGuiApplication) -> QIcon:
    icon = QIcon(str(APP_ICON_PATH))
    if icon.isNull():
        _LOG.warning("application icon could not be loaded: %s", APP_ICON_PATH)
    else:
        app.setWindowIcon(icon)
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(PRODUCT_DESKTOP_ID)
    return icon


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id(PRODUCT_DESKTOP_ID)
        if result != 0:
            _LOG.warning("Windows AppUserModelID could not be set: HRESULT %s", result)
    except (AttributeError, OSError) as exc:
        _LOG.warning("Windows AppUserModelID could not be set: %s", exc)


def create_engine(
    app: QGuiApplication | None = None,
    *,
    data_paths: DataPaths | None = None,
    startup_timing: StartupTiming | None = None,
) -> tuple[QQmlApplicationEngine, SetupBridge]:
    # QQuickStyle must be selected before Qt Quick Controls QML is loaded.
    _configure_quick_controls_style(sys.platform)
    if app is None:
        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    timing = startup_timing if startup_timing is not None else StartupTiming()
    QCoreApplication.setOrganizationName(PRODUCT_ORGANIZATION)
    QCoreApplication.setApplicationName(PRODUCT_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    icon = _configure_app_icon(app)
    paths = data_paths or resolve_data_paths()
    paths.ensure()
    runtime = AudioRuntime()
    bridge = SetupBridge(
        paths.metadata,
        paths.waveform_cache,
        paths.music,
        paths.archive_metadata,
        paths.archive_waveform_cache,
        runtime=runtime,
        export_directory=(
            paths.root.parent if data_paths is not None else resolve_program_root()
        ) / "exports",
    )
    metadata_store = MetadataStore(
        paths.metadata,
        music_directory=paths.music,
        waveform_cache_directory=paths.waveform_cache,
    )
    player_bridge = PlayerBridge(
        runtime, metadata_store, paths.root / "player-settings.json"
    )
    timing.track_count = len(player_bridge.controller.tracks)
    qmlRegisterType(WaveformItem, "UnderscorePnP", 1, 0, "WaveformItem")
    engine = QQmlApplicationEngine()
    # QQmlContext does not take Python ownership. Keep the bridge alive for at
    # least as long as every binding in the engine can evaluate it.
    engine._setup_bridge = bridge
    engine._player_bridge = player_bridge
    engine._audio_runtime = runtime
    engine._startup_timing = timing
    shutdown = ShutdownController(bridge, player_bridge, runtime)
    engine._shutdown_controller = shutdown
    engine.rootContext().setContextProperty("setupBackend", bridge)
    engine.rootContext().setContextProperty("playerBackend", player_bridge)
    engine.rootContext().setContextProperty("shutdownBackend", shutdown)
    qml_path = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if engine.rootObjects() and isinstance(engine.rootObjects()[0], QQuickWindow):
        window = engine.rootObjects()[0]
        if not icon.isNull():
            window.setIcon(icon)
        window.frameSwapped.connect(timing.record_first_frame)
    app.aboutToQuit.connect(shutdown.close)
    return engine, bridge


def main() -> int:
    entry_monotonic_ns = monotonic_ns()
    entry_epoch_ns = time_ns()
    _set_windows_app_id()
    app = QGuiApplication(sys.argv)
    QCoreApplication.setOrganizationName(PRODUCT_ORGANIZATION)
    QCoreApplication.setApplicationName(PRODUCT_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    log_runtime = configure_logging()
    engine: QQmlApplicationEngine | None = None
    try:
        paths = resolve_data_paths()
        _LOG.info(
            "application starting version=%s python=%s qt=%s os=%s architecture=%s "
            "data_directory=%s log_directory=%s",
            __version__,
            platform.python_version(),
            qVersion(),
            platform.platform(),
            platform.machine(),
            paths.root,
            log_runtime.log_directory,
        )
        engine, _bridge = create_engine(
            app,
            data_paths=paths,
            startup_timing=StartupTiming(
                entry_monotonic_ns=entry_monotonic_ns,
                entry_epoch_ns=entry_epoch_ns,
            ),
        )
        if not engine.rootObjects():
            _LOG.critical("QML application window could not be created")
            return 2
        exit_code = app.exec()
        _LOG.info("application event loop stopped exit_code=%d", exit_code)
        return exit_code
    except Exception:
        _LOG.exception("application startup or event loop failed")
        return 1
    finally:
        if engine is not None:
            _dispose_qml_engine(engine)
        _LOG.info("application shutdown complete")
        log_runtime.close()


def _dispose_qml_engine(engine: QQmlApplicationEngine) -> None:
    """Destroy QML while every Python backend is still strongly referenced."""
    # QQmlContext does not own Python QObjects. If the engine's Python wrapper
    # releases its attribute dictionary first, QML bindings briefly observe
    # null backends while the visual tree is still being dismantled. Local
    # references enforce the opposite, deterministic destruction order.
    keep_alive = (
        engine._setup_bridge,
        engine._player_bridge,
        engine._audio_runtime,
        engine._startup_timing,
        engine._shutdown_controller,
    )
    engine._shutdown_controller.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # Keep the tuple live through sendPostedEvents; this is intentionally not
    # replaced by a mere attribute access above.
    del keep_alive
