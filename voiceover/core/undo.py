"""История изменений проекта (отмена/повтор) на основе снимков состояния.

Снимок — это реплики, персонажи и параметры сведения. Изменения фиксируются
одной точкой (commit) после любого действия; несколько быстрых правок подряд
(например, набор текста) склеиваются в один шаг на стороне интерфейса.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Project


@dataclass
class _Step:
    state: dict
    label: str


class UndoHistory:
    def __init__(self, limit: int = 200):
        self.limit = limit
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._current: Optional[dict] = None

    def reset(self, project: Optional[Project]) -> None:
        self._undo.clear()
        self._redo.clear()
        self._current = project.snapshot() if project else None

    def commit(self, project: Project, label: str = "Изменение") -> bool:
        """Зафиксировать текущее состояние. True — если оно действительно изменилось."""
        new = project.snapshot()
        if self._current is None:
            self._current = new
            return False
        if new == self._current:
            return False
        self._undo.append(_Step(self._current, label))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()
        self._current = new
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def undo(self, project: Project) -> Optional[str]:
        if not self._undo or self._current is None:
            return None
        step = self._undo.pop()
        self._redo.append(_Step(self._current, step.label))
        project.restore(step.state)
        self._current = step.state
        return step.label

    def redo(self, project: Project) -> Optional[str]:
        if not self._redo or self._current is None:
            return None
        step = self._redo.pop()
        self._undo.append(_Step(self._current, step.label))
        project.restore(step.state)
        self._current = step.state
        return step.label
