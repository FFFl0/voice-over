"""Дубли реплики: все записи rec/<id>_<N>.wav, выбор того, что идёт в сведение."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .models import Line, Project


@dataclass
class Take:
    rel: str           # путь относительно проекта (как в Line.rec_path)
    path: Path
    number: int
    offset: float
    duration: float
    mtime: float


def _num(p: Path) -> int:
    m = re.search(r"_(\d+)$", p.stem)
    return int(m.group(1)) if m else 0


def list_takes(project: Project, line: Line) -> list[Take]:
    d = project.project_dir / "rec"
    if not d.exists():
        return []
    out = []
    for f in d.glob(f"{line.id}_*.wav"):
        rel = project.relative(f)
        if rel == line.rec_path:
            off = line.rec_offset
        else:
            off = float(line.takes.get(rel, 0.0))
        try:
            dur = sf.info(str(f)).duration
        except Exception:  # noqa: BLE001  повреждённый файл
            continue
        out.append(Take(rel, f, _num(f), off, dur, f.stat().st_mtime))
    return sorted(out, key=lambda t: t.number)


def remember_offset(line: Line) -> None:
    if line.rec_path:
        line.takes[line.rec_path] = round(line.rec_offset, 3)


def select_take(line: Line, rel: str) -> None:
    """Сделать дубль текущим (пустая строка — реплика без записи)."""
    remember_offset(line)
    if rel != line.rec_path:
        reset_edits(line)
    line.rec_path = rel
    line.rec_offset = float(line.takes.get(rel, 0.0)) if rel else 0.0


def reset_edits(line: Line) -> None:
    """Обрезка и фейды относятся к конкретному дублю — у нового начинаются с нуля."""
    line.trim_in = line.trim_out = line.fade_in = line.fade_out = 0.0


def delete_take(project: Project, line: Line, rel: str) -> None:
    """Удалить файл дубля; если он был текущим — выбрать последний из оставшихся."""
    p = project.resolve(rel)
    if p is not None:
        p.unlink(missing_ok=True)
    line.takes.pop(rel, None)
    if line.rec_path == rel:
        rest = list_takes(project, line)
        line.rec_path = ""
        line.rec_offset = 0.0
        if rest:
            select_take(line, rest[-1].rel)
