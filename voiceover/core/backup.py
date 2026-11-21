"""Резервные копии проекта: перед сохранением прежняя версия .vop копируется в папку
backups проекта (не чаще раза в несколько минут, хранятся последние N)."""

from __future__ import annotations

import datetime
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .models import Project

KEEP = 30
MIN_INTERVAL = 300.0   # сек между копиями при частых сохранениях


@dataclass
class Backup:
    path: Path
    when: datetime.datetime
    lines: int
    recorded: int


def backup_dir(project: Project) -> Path:
    return project.project_dir / "backups"


def make_backup(project: Project, min_interval: float = MIN_INTERVAL, keep: int = KEEP,
                force: bool = False) -> Path | None:
    """Скопировать текущий файл проекта (до перезаписи). None — копия не нужна."""
    if not project.path or not Path(project.path).exists():
        return None
    d = backup_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(d.glob("*.vop"), key=lambda p: p.stat().st_mtime)
    if existing and not force and time.time() - existing[-1].stat().st_mtime < min_interval:
        return None
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dst = d / f"{Path(project.path).stem}_{stamp}.vop"
    n = 2
    while dst.exists():
        dst = d / f"{Path(project.path).stem}_{stamp}_{n}.vop"
        n += 1
    shutil.copy2(project.path, dst)
    dst.touch()   # время копии — время её создания
    existing.append(dst)
    for old in existing[:-keep]:
        old.unlink(missing_ok=True)
    return dst


def list_backups(project: Project) -> list[Backup]:
    out = []
    for f in backup_dir(project).glob("*.vop"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            lines = d.get("lines", [])
            out.append(Backup(f, datetime.datetime.fromtimestamp(f.stat().st_mtime), len(lines),
                              sum(1 for ln in lines if ln.get("rec_path"))))
        except Exception:  # noqa: BLE001  повреждённую копию просто не показываем
            continue
    return sorted(out, key=lambda b: b.when, reverse=True)


def load_backup_state(path: Path) -> dict:
    """Состояние (как снимок отмены) из резервной копии."""
    q = Project.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    return q.snapshot()
