"""Keep the window responsive until its background jobs have finished."""

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot


class ShutdownController(QObject):
    changed = Signal()
    ready = Signal()

    def __init__(self, setup, player, runtime) -> None:
        super().__init__()
        self._setup = setup
        self._player = player
        self._runtime = runtime
        self._closing = False
        self._complete = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._check_workers)

    @Property(bool, notify=changed)
    def closing(self) -> bool:
        return self._closing

    @Property(bool, notify=changed)
    def complete(self) -> bool:
        return self._complete

    @Slot()
    def request(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.changed.emit()
        self._setup.begin_shutdown()
        self._player.begin_shutdown()
        self._runtime.engine.stop()
        self._timer.start()

    @Slot()
    def _check_workers(self) -> None:
        if self._setup.worker_running or self._player.worker_running:
            return
        self._timer.stop()
        self._complete = True
        self.changed.emit()
        self.ready.emit()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._setup.close()
        self._player.close()
        self._runtime.close()
