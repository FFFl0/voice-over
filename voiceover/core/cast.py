"""Библиотека составов: голоса персонажей сериала переносятся между сериями.

Состав — именованный набор персонажей с настройками озвучки (движок, голос, темп,
высота, громкость, цвет, актёр). При загрузке состава в проект настройки применяются
к персонажам с тем же именем (без учёта регистра); недостающих можно добавить.
Хранится в cast_library.json в папке настроек программы.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Character, Project

VOICE_FIELDS = ("color", "source", "engine", "voice", "rate", "pitch", "volume_db", "actor")


def library_path() -> Path:
    from .settings import data_dir
    return data_dir() / "cast_library.json"


def _load() -> dict:
    try:
        d = json.loads(library_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("casts"), dict) else {"casts": {}}
    except Exception:  # noqa: BLE001  нет файла или повреждён
        return {"casts": {}}


def _save(d: dict) -> None:
    p = library_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def list_casts() -> list[str]:
    return sorted(_load()["casts"], key=str.lower)


def get_cast(name: str) -> list[dict]:
    return list(_load()["casts"].get(name, []))


def _key(name: str) -> str:
    return name.strip().lower()


def save_cast(name: str, characters: list[Character], merge: bool = True) -> int:
    """Сохранить голоса персонажей в состав. merge — дополнить существующий состав
    (персонажи из других серий сохраняются). Возвращает число персонажей в составе."""
    d = _load()
    existing = {_key(c["name"]): c for c in d["casts"].get(name, [])} if merge else {}
    for ch in characters:
        full = ch.to_dict()
        entry = {"name": ch.name, **{k: full[k] for k in VOICE_FIELDS}}
        existing[_key(ch.name)] = entry
    d["casts"][name] = sorted(existing.values(), key=lambda c: c["name"].lower())
    _save(d)
    return len(d["casts"][name])


def delete_cast(name: str) -> None:
    d = _load()
    d["casts"].pop(name, None)
    _save(d)


def apply_entry(ch: Character, entry: dict) -> None:
    tmp = Character.from_dict({"name": ch.name, **{k: entry[k] for k in VOICE_FIELDS if k in entry}})
    for k in VOICE_FIELDS:
        setattr(ch, k, getattr(tmp, k))


def apply_cast(project: Project, name: str, add_missing: bool = False,
               only: Optional[list[Character]] = None) -> tuple[int, int]:
    """Применить состав к проекту. only — применить только к этим персонажам
    (например, к только что созданным импортом). Возвращает (обновлено, добавлено)."""
    entries = {_key(c["name"]): c for c in get_cast(name)}
    targets = only if only is not None else project.characters
    updated = 0
    for ch in targets:
        e = entries.get(_key(ch.name))
        if e:
            apply_entry(ch, e)
            updated += 1
    added = 0
    if add_missing and only is None:
        have = {_key(c.name) for c in project.characters}
        for k, e in entries.items():
            if k not in have:
                ch = project.add_character(e["name"])
                apply_entry(ch, e)
                added += 1
    project.cast_name = name
    return updated, added
