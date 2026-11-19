"""Фоновые задачи с прогрессом и отменой."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core.ffmpeg import Cancelled


class TaskSignals(QObject):
    progress = Signal(float, str)
    result = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()
    message = Signal(object)   # произвольные промежуточные данные (например, результат по реплике)


class Task(QRunnable):
    """fn(task) -> result. Внутри fn используйте task.report(), task.is_cancelled(), task.emit()."""

    def __init__(self, title: str, fn: Callable[["Task"], Any]):
        super().__init__()
        self.title = title
        self.fn = fn
        self.signals = TaskSignals()
        self._cancel = False
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def report(self, frac: float, text: str = "") -> None:
        self.signals.progress.emit(float(frac), text)

    def emit(self, obj: Any) -> None:
        self.signals.message.emit(obj)

    def run(self) -> None:
        try:
            res = self.fn(self)
            if self._cancel:
                self.signals.cancelled.emit()
            else:
                self.signals.result.emit(res)
        except Cancelled:
            self.signals.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.signals.error.emit(str(e) or e.__class__.__name__)
        finally:
            self.signals.finished.emit()


def pool() -> QThreadPool:
    return QThreadPool.globalInstance()
