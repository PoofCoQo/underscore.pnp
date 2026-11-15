"""QQuickPaintedItem rendering a cache-backed stereo waveform."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QLineF, QRectF, Signal, Slot, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtQuick import QQuickPaintedItem


class WaveformItem(QQuickPaintedItem):
    backendChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._backend: QObject | None = None
        self.setAntialiasing(False)

    def get_backend(self) -> QObject | None:
        return self._backend

    def set_backend(self, backend: QObject | None) -> None:
        if backend is self._backend:
            return
        if self._backend is not None:
            try:
                self._backend.waveformChanged.disconnect(self.update)
            except (RuntimeError, TypeError):
                pass
        self._backend = backend
        if backend is not None:
            backend.waveformChanged.connect(self.update)
        self.backendChanged.emit()
        self.update()

    backend = Property(QObject, get_backend, set_backend, notify=backendChanged)

    @Slot()
    def refresh(self) -> None:
        self.update()

    def paint(self, painter: QPainter) -> None:
        width, height = max(1, int(self.width())), max(1, int(self.height()))
        painter.fillRect(QRectF(0, 0, width, height), QColor("#11161d"))
        backend = self._backend
        if backend is None or not backend.hasTrack:
            return
        start, end = backend.visibleStart, backend.visibleEnd
        duration = max(1, end - start)

        # Detected outer silence is deliberately shown behind the waveform.
        painter.setBrush(QColor(70, 75, 84, 105))
        painter.setPen(Qt.PenStyle.NoPen)
        for region_start, region_end in ((0, backend.detectedPlayStart), (backend.detectedPlayEnd, backend.totalFrames)):
            left = (region_start - start) * width / duration
            right = (region_end - start) * width / duration
            painter.drawRect(QRectF(max(0, left), 0, max(0, min(width, right) - max(0, left)), height))

        for frames, color, line_width in (
            (backend.visibleBeats(), QColor("#374656"), 1),
            (backend.visibleBars(), QColor("#687f98"), 2),
        ):
            painter.setPen(QPen(color, line_width))
            painter.drawLines(
                [
                    QLineF((frame - start) * width / duration, 0,
                           (frame - start) * width / duration, height)
                    for frame in frames
                ]
            )

        waveform = backend.waveform_slice(width)
        if waveform is None or waveform.values.size == 0:
            return
        values = waveform.values
        bin_size = waveform.bin_size
        painter.setPen(QPen(QColor("#54d6c7"), 1))
        for channel in range(2):
            top = channel * height / 2
            center = top + height / 4
            amplitude = height / 4 - 3
            painter.drawLines(
                [
                    QLineF(
                        ((waveform.first_bin + index + 0.5) * bin_size - start)
                        * width / duration,
                        center - float(pair[1]) * amplitude,
                        ((waveform.first_bin + index + 0.5) * bin_size - start)
                        * width / duration,
                        center - float(pair[0]) * amplitude,
                    )
                    for index, pair in enumerate(values[:, channel, :])
                ]
            )
        painter.setPen(QPen(QColor("#26313d"), 1))
        painter.drawLine(0, height / 2, width, height / 2)
