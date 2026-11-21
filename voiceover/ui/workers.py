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
        def emit(sig, *args):
            try:
                sig.emit(*args)
            except RuntimeError:   # окно уже закрыто — результат никому не нужен
                pass
        try:
            res = self.fn(self)
            if self._cancel:
                emit(self.signals.cancelled)
            else:
                emit(self.signals.result, res)
        except Cancelled:
            emit(self.signals.cancelled)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            emit(self.signals.error, str(e) or e.__class__.__name__)
        finally:
            emit(self.signals.finished)


def pool() -> QThreadPool:
    return QThreadPool.globalInstance()
