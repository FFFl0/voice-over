"""Удалённая запись: пакет для актёра и обратная загрузка его записей.

1. Режиссёр выгружает «пакет для актёра» — zip с копией проекта (без записей и служебных
   файлов) и сценарием его ролей. Видео в пакет не кладётся: у актёра своя копия серии.
2. Актёр открывает пакет в этой же программе, записывает реплики и нажимает
   «Отправить записи» — получается zip с выбранными дублями и manifest.json.
3. Режиссёр загружает этот zip: записи встают в свои реплики по их id как новые дубли.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .models import PROJECT_EXT, Project
from .subtitles import fmt_clock
from .takes import list_takes, select_take

MANIFEST = "manifest.json"
FORMAT = "voiceover-recordings/1"


def _script(project: Project, cids: set[str]) -> str:
    out = []
    for c in project.characters:
        if c.id not in cids:
            continue
        lines = [ln for ln in project.lines if ln.character_id == c.id and ln.enabled]
        out.append(f"{c.name} — {len(lines)} реплик\n" + "=" * 40)
        for ln in lines:
            note = f"   ({ln.note})" if ln.note else ""
            out.append(f"{fmt_clock(ln.start)}  {ln.text}{note}")
        out.append("")
    return "\n".join(out)


def export_actor_package(project: Project, character_ids: Iterable[str], out_zip: str) -> int:
    """Пакет для актёра. Возвращает число реплик его ролей."""
    cids = set(character_ids)
    if not cids:
        raise ValueError("Не выбраны роли")
    d = project.to_dict()
    d["video_path"] = Path(project.video_path).name if project.video_path else ""
    d["original_wav"] = d["background_wav"] = ""
    d["package"] = {"characters": sorted(cids),
                    "source": Path(project.path).stem if project.path else "проект"}
    for ln in d["lines"]:
        ln["rec_path"], ln["rec_offset"], ln["takes"] = "", 0.0, {}
    names = [c.name for c in project.characters if c.id in cids]
    stem = (Path(project.path).stem if project.path else "проект")
    n = sum(1 for ln in project.lines if ln.character_id in cids and ln.enabled)
    readme = (
        "Пакет для записи в VoiceOver Studio\n\n"
        f"Роли: {', '.join(names)}\n\n"
        "1. Распакуйте архив в отдельную папку (или откройте его через\n"
        "   «Файл → Открыть пакет актёра»).\n"
        f"2. Когда программа спросит видео, укажите свою копию серии ({d['video_path']}).\n"
        "3. Запишите реплики на вкладке «Запись».\n"
        "4. «Файл → Отправить записи…» — отправьте получившийся zip режиссёру.\n")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(stem + PROJECT_EXT, json.dumps(d, ensure_ascii=False, indent=1))
        z.writestr("Сценарий.txt", _script(project, cids))
        z.writestr("Прочтите.txt", readme)
    return n


def open_actor_package(zip_path: str, dest_dir: str) -> str:
    """Распаковать пакет (только проект и тексты) и вернуть путь к .vop."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    vop = None
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            base = Path(name).name          # без папок: ничего не пишем за пределы dest
            if not base or not (base.endswith(PROJECT_EXT) or base.endswith(".txt")):
                continue
            target = dest / base
            target.write_bytes(z.read(name))
            if base.endswith(PROJECT_EXT):
                vop = target
    if vop is None:
        raise ValueError("В архиве нет проекта — это не пакет для актёра")
    # видео с тем же именем рядом с архивом или в папке распаковки — подставим сразу
    d = json.loads(vop.read_text(encoding="utf-8"))
    name = Path(d.get("video_path") or "").name
    for cand in (Path(zip_path).parent / name, dest / name, dest.parent / name):
        if name and cand.is_file():
            d["video_path"] = str(cand.resolve())
            vop.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            break
    return str(vop)


def export_recordings(project: Project, out_zip: str,
                      character_ids: Optional[Iterable[str]] = None) -> int:
    """Выбранные дубли реплик (ролей пакета или указанных) в zip для режиссёра."""
    cids = set(character_ids or project.package.get("characters") or
               [c.id for c in project.characters])
    items = []
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as z:
        for ln in project.lines:
            if ln.character_id not in cids or not ln.rec_path:
                continue
            src = project.resolve(ln.rec_path)
            if not src or not src.exists():
                continue
            name = f"rec/{ln.id}.wav"
            z.write(src, name)
            items.append({"id": ln.id, "file": name, "offset": ln.rec_offset,
                          "start": ln.start, "end": ln.end, "text": ln.text})
        z.writestr(MANIFEST, json.dumps({"format": FORMAT, "items": items},
                                        ensure_ascii=False, indent=1))
    return len(items)


@dataclass
class ImportReport:
    imported: int = 0
    unknown: int = 0                       # реплик с таким id в проекте нет
    moved: list = field(default_factory=list)   # тексты реплик, у которых сдвинулся тайминг


def import_recordings(project: Project, zip_path: str) -> ImportReport:
    """Загрузить записи актёра: каждая становится новым дублем своей реплики и выбирается."""
    rep = ImportReport()
    by_id = {ln.id: ln for ln in project.lines}
    rec = project.project_dir / "rec"
    rec.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if MANIFEST not in names:
            raise ValueError("В архиве нет manifest.json — это не записи из VoiceOver Studio")
        man = json.loads(z.read(MANIFEST).decode("utf-8"))
        for it in man.get("items", []):
            ln = by_id.get(str(it.get("id", "")))
            fname = str(it.get("file", ""))
            if ln is None or fname not in names:
                rep.unknown += 1
                continue
            n = max([t.number for t in list_takes(project, ln)] + [0]) + 1
            target = rec / f"{ln.id}_{n}.wav"
            with z.open(fname) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            rel = project.relative(target)
            # сдвиг считался от начала реплики у актёра; если режиссёр с тех пор
            # подвинул реплику, сохраняем привязку к звуку фильма
            offset = float(it.get("offset", 0.0)) + float(it.get("start", ln.start)) - ln.start
            ln.takes[rel] = round(offset, 3)
            select_take(ln, rel)
            ln.rec_offset = round(offset, 3)
            if abs(float(it.get("start", ln.start)) - ln.start) > 0.05:
                rep.moved.append(ln.text)
            rep.imported += 1
    return rep
