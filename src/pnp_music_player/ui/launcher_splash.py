"""Minimal, standalone splash launched alongside the application.

Run this file directly: importing the application package would delay the splash.
"""

from pathlib import Path
import os
import runpy
import sys

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen


SPLASH_WIDTH = 760
SPLASH_HEIGHT = 340
CLAIM = "At least the music stays on track."
PROJECT_NAME = "A PoofCoQo Project"
PROJECT_CLAIM = "It works. We have questions too."


def _read_version() -> str:
    version_file = Path(__file__).resolve().parents[1] / "_version.py"
    return str(runpy.run_path(str(version_file))["__version__"])


def _draw_text(
    painter: QPainter,
    text: str,
    bounds: QRect,
    *,
    pixel_size: int,
    color: str,
    weight: QFont.Weight = QFont.Weight.Normal,
) -> None:
    font = QFont(QApplication.font())
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, text)


def _build_splash_pixmap(version: str) -> QPixmap:
    pixmap = QPixmap(SPLASH_WIDTH, SPLASH_HEIGHT)
    pixmap.fill(QColor("#17222c"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    painter.setPen(QPen(QColor("#405466"), 1))
    painter.drawRect(0, 0, SPLASH_WIDTH - 1, SPLASH_HEIGHT - 1)

    _draw_text(
        painter,
        "underscore.pnp",
        QRect(40, 45, SPLASH_WIDTH - 80, 62),
        pixel_size=43,
        color="#f1f6fa",
        weight=QFont.Weight.Bold,
    )
    _draw_text(
        painter,
        CLAIM,
        QRect(40, 112, SPLASH_WIDTH - 80, 38),
        pixel_size=20,
        color="#76cfc0",
        weight=QFont.Weight.DemiBold,
    )
    _draw_text(
        painter,
        f"v{version}",
        QRect(40, 164, SPLASH_WIDTH - 80, 24),
        pixel_size=14,
        color="#9fb2c4",
    )

    painter.setPen(QPen(QColor("#405466"), 1))
    painter.drawLine(160, 215, SPLASH_WIDTH - 160, 215)
    _draw_text(
        painter,
        PROJECT_NAME,
        QRect(40, 238, SPLASH_WIDTH - 80, 30),
        pixel_size=17,
        color="#e7eef5",
        weight=QFont.Weight.DemiBold,
    )
    _draw_text(
        painter,
        PROJECT_CLAIM,
        QRect(40, 277, SPLASH_WIDTH - 80, 28),
        pixel_size=15,
        color="#9fb2c4",
    )
    painter.end()
    return pixmap


def main() -> int:
    # The main process, not this temporary window, should own desktop activation.
    os.environ.pop("XDG_ACTIVATION_TOKEN", None)
    os.environ.pop("DESKTOP_STARTUP_ID", None)
    splash_dir = os.environ.get("PNP_MUSIC_SPLASH_DIR")
    if splash_dir and not Path(splash_dir).is_dir():
        return 0
    app = QApplication(sys.argv)
    icon_path = Path(__file__).with_name("assets") / "app-icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    pixmap = _build_splash_pixmap(_read_version())
    splash = QSplashScreen(
        pixmap,
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
    )
    if splash_dir:
        timer = QTimer(splash)
        timer.timeout.connect(
            lambda: app.quit() if not Path(splash_dir).is_dir() else None
        )
        timer.start(30)
    splash.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
