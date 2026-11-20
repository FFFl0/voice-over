"""История изменений проекта (отмена/повтор) на основе снимков состояния.

Снимок — это реплики, персонажи и параметры сведения. Изменения фиксируются
одной точкой (commit) после любого действия; несколько быстрых правок подряд
(например, набор текста) склеиваются в один шаг на стороне интерфейса.

Снимки хранятся сжатым JSON (zlib): на фильм в 3000 реплик шаг весит ~60 КБ
вместо ~1.4 МБ в виде словарей Python, поэтому 200 шагов — около 12 МБ.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from typing import Iterator, Optional

from .models import Project


def pack(state: dict) -> bytes:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, 1)


def unpack(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


@dataclass
class _Step:
    blob: bytes
    label: str

    @property
    def state(self) -> dict:
        return unpack(self.blob)


class UndoHistory:
    def __init__(self, limit: int = 200):
        self.limit = limit
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._current: Optional[bytes] = None

    def reset(self, project: Optional[Project]) -> None:
        self._undo.clear()
        self._redo.clear()
        self._current = pack(project.snapshot()) if project else None

    def commit(self, project: Project, label: str = "Изменение") -> bool:
        """Зафиксировать текущее состояние. True — если оно действительно изменилось."""
        new = pack(project.snapshot())
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
        project.restore(unpack(step.blob))
        self._current = step.blob
        return step.label

    def redo(self, project: Project) -> Optional[str]:
        if not self._redo or self._current is None:
            return None
        step = self._redo.pop()
        self._undo.append(_Step(self._current, step.label))
        project.restore(unpack(step.blob))
        self._current = step.blob
        return step.label

    def states(self) -> Iterator[dict]:
        """Все состояния в истории (для поиска файлов, на которые ещё можно вернуться)."""
        for step in self._undo + self._redo:
            yield step.state

    def memory_bytes(self) -> int:
        return sum(len(s.blob) for s in self._undo + self._redo) + len(self._current or b"")
