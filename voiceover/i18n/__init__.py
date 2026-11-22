"""Языки интерфейса.

Русский текст в коде — ключ; переводы лежат рядом в <язык>.json ({"русский текст": "перевод"}).
Перевод подставляется там, где текст попадает на экран: в подписи, кнопки, меню, подсказки,
окна сообщений, строку состояния и нарисованные надписи (install() подменяет эти методы Qt).
Сообщения с подстановками переводятся по шаблонам: ключ «Удалено реплик: {}» узнаёт
«Удалено реплик: 5». Без перевода строка остаётся русской — данные проекта (имена
персонажей, текст реплик) поэтому не трогаются."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

LANGUAGES = {"ru": "Русский", "en": "English", "uk": "Українська"}
QT_NAMES = {"ru": "ru", "en": "en", "uk": "uk"}

_lang = "ru"
_exact: dict[str, str] = {}
_templates: list[tuple[re.Pattern, str, int, int]] = []
_CYR = re.compile(r"[а-яёА-ЯЁ]")
_SHORTCUT = re.compile(r"(.*\S)(\s*\([^()а-яёА-ЯЁ]*\))\Z", re.S)   # «Экспорт (Ctrl+E)»


class NoTr(str):
    """Строка, которую не нужно переводить (например, название языка в его же языке)."""


def current() -> str:
    return _lang


def auto_language() -> str:
    """Язык по умолчанию — язык системы, если он есть среди переводов, иначе английский."""
    try:
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()[:2]
    except Exception:  # noqa: BLE001
        return "ru"
    if name in ("ru", "be", "kk"):
        return "ru"
    return name if name in LANGUAGES else "en"


def _compile(key: str) -> Optional[tuple[re.Pattern, int]]:
    parts = key.split("{}")
    literal = "".join(parts)
    if len(_CYR.findall(literal)) < 2:      # «{} — {}» и подобное ничего не значит
        return None
    rx = "(.*?)".join(re.escape(p) for p in parts)
    return re.compile(rx + r"\Z", re.S), len(literal), len(_CYR.findall(literal))


def set_language(lang: str) -> str:
    global _lang, _exact, _templates
    lang = lang if lang in LANGUAGES else "ru"
    _lang = lang
    _exact, _templates = {}, []
    _cached.cache_clear()
    if lang == "ru":
        return lang
    try:
        data = json.loads((Path(__file__).with_name(f"{lang}.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return lang
    for k, v in data.items():
        if not v:
            continue
        if "{}" in k:
            c = _compile(k)
            if c:
                _templates.append((c[0], v, c[1], c[2]))
        _exact[k] = v
    _templates.sort(key=lambda t: -t[2])
    return lang


def _fill(template: str, groups: tuple) -> str:
    """Подставить значения в перевод: {} по порядку или {0}, {1} — по номеру."""
    vals = [tr(g, _depth=1) for g in groups]
    if re.search(r"\{\d+\}", template):
        return re.sub(r"\{(\d+)\}", lambda m: vals[int(m.group(1))] if int(m.group(1)) < len(vals)
                      else m.group(0), template)
    it = iter(vals)
    return re.sub(r"\{\}", lambda m: next(it, ""), template)


@lru_cache(maxsize=4096)
def _cached(s: str, depth: int) -> str:
    hit = _exact.get(s)
    if hit is not None:
        return hit
    stripped = s.strip()
    if stripped != s and stripped in _exact:          # « Добавить», «Готово\n»
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        return lead + _exact[stripped] + trail
    m = _SHORTCUT.match(s) if ";;" not in s and "\n" not in s else None
    if m:
        head = _cached(m.group(1), depth)
        if head != m.group(1):
            return head + m.group(2)
    if depth < 2:
        for rx, tmpl, _n, letters in _templates:
            m = rx.match(s)
            # короткий шаблон («из {}», «{} с») — только для чисел и имён файлов,
            # иначе «Импорт реплик из …» распадётся на кусочки
            if m and (letters >= 4 or not any(_CYR.search(g) for g in m.groups())):
                return _fill(tmpl, m.groups())
    # составной текст: фильтры «A;;B», абзацы, строки — по частям
    for sep in (";;", "\n\n", "\n", ", "):
        if sep in s:
            parts = s.split(sep)
            out = [_cached(p, depth) if p else p for p in parts]
            if out != parts:
                return sep.join(out)
            break
    return s


def tr(s, _depth: int = 0):
    """Перевод строки на текущий язык (не строки и NoTr — как есть)."""
    if _lang == "ru" or not isinstance(s, str) or isinstance(s, NoTr) or not s \
            or not _CYR.search(s):
        return s
    return _cached(s, _depth)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа для текущего языка: русские формы — ключи перевода."""
    if _lang == "en":
        return tr(one) if n == 1 else tr(many)
    if n % 10 == 1 and n % 100 != 11:
        w = one
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        w = few
    else:
        w = many
    return tr(w)


def coverage(texts) -> tuple[int, list[str]]:
    """Сколько строк переводится и какие — нет (для проверки словарей)."""
    missing = [t for t in texts if tr(t) == t]
    return len(texts) - len(missing), missing
