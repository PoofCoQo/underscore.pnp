"""Run one blocking QObject worker without a per-worker Qt event loop."""

from PySide6.QtCore import QObject, QThread


class WorkerThread(QThread):
    """Finish the QThread when the worker returns, even during shutdown."""

    def __init__(self, worker: QObject, parent: QObject) -> None:
        super().__init__(parent)
        self._worker = worker
        worker.moveToThread(self)
        self.finished.connect(worker.deleteLater)

    def run(self) -> None:
        self._worker.run()
