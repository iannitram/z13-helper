"""One background thread that runs hardware writes in order.

Profile applies and hooks (boondock) can take seconds; running them on the
Qt thread freezes the window, running them on ad-hoc threads lets two slider
releases race each other. A single queue keeps the order the user clicked in.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal


class ApplyWorker(QObject):
    done = pyqtSignal(str, object)   # (label, result-or-Exception)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._q = queue.Queue()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def submit(self, label: str, fn: Callable[[], object]) -> None:
        self._q.put((label, fn))

    def _loop(self) -> None:
        while True:
            label, fn = self._q.get()
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                result = e
            self.done.emit(label, result)
