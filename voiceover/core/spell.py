"""Проверка орфографии по словарям Hunspell из LibreOffice (движок spylls, чистый Python).

Словарь скачивается один раз (~3.5 МБ) в папку настроек. Свои слова (имена, термины)
добавляются в личный словарь; имена персонажей проекта считаются известными.
Ударения (U+0301) и буква «ё» проверке не мешают."""

from __future__ import annotations

import re
import threading
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

ACCENT = "́"
BASE_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/"
LANGS = {"ru": ("ru_RU", "ru_RU"), "en": ("en", "en_US"), "uk": ("uk_UA", "uk_UA")}
WORD_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|́)*(?:-[^\W\d_](?:[^\W\d_]|́)*)*")
_CYR = re.compile(r"[а-яёА-ЯЁіїєґІЇЄҐ]")
_LAT = re.compile(r"[A-Za-z]")


def dict_dir() -> Path:
    from .settings import data_dir
    d = data_dir() / "dict"
    d.mkdir(parents=True, exist_ok=True)
    return d


def strip_accents(s: str) -> str:
    return s.replace(ACCENT, "")


def ensure_dictionary(lang: str, folder: Optional[Path] = None) -> Path:
    """Путь к словарю без расширения; скачать при первом обращении."""
    sub, name = LANGS[lang]
    d = folder or dict_dir()
    base = d / name
    for ext in (".aff", ".dic"):
        p = base.with_suffix(ext)
        if not p.exists() or p.stat().st_size == 0:
            tmp = p.with_suffix(ext + ".part")
            with urllib.request.urlopen(f"{BASE_URL}{sub}/{name}{ext}", timeout=60) as r:
                tmp.write_bytes(r.read())
            tmp.replace(p)
    return base


class SpellChecker:
    def __init__(self, lang: str = "ru", folder: Optional[Path] = None):
        self.lang = lang
        self.folder = folder
        self.dic = None
        self.error = ""
        self.extra: set[str] = set()        # имена персонажей проекта и т. п.
        self._cache: dict[str, bool] = {}
        self._lock = threading.Lock()
        self.user_words = self._load_user()

    # ------------------------------------------------------------ личный словарь
    def _user_path(self) -> Path:
        return (self.folder or dict_dir()) / "user.txt"

    def _load_user(self) -> set[str]:
        try:
            return {w.strip().lower() for w in self._user_path().read_text(encoding="utf-8")
                    .splitlines() if w.strip()}
        except OSError:
            return set()

    def add_word(self, word: str) -> None:
        w = strip_accents(word).lower()
        self.user_words.add(w)
        self._cache.pop(w, None)
        try:
            self._user_path().write_text("\n".join(sorted(self.user_words)) + "\n", encoding="utf-8")
        except OSError:
            pass

    def set_extra(self, words: Iterable[str]) -> None:
        new = set()
        for w in words:
            new |= {x.lower() for x in WORD_RE.findall(strip_accents(w))}
        if new != self.extra:
            self.extra = new
            self._cache.clear()

    # ------------------------------------------------------------ загрузка
    def load(self) -> bool:
        """Загрузить словарь (долго — вызывать в фоне). False — недоступен."""
        try:
            from spylls.hunspell import Dictionary
            base = ensure_dictionary(self.lang, self.folder)
            dic = Dictionary.from_files(str(base))
        except Exception as e:  # noqa: BLE001  нет модуля или интернета
            self.error = str(e) or e.__class__.__name__
            return False
        with self._lock:
            self.dic = dic
            self._cache.clear()
        return True

    @property
    def ready(self) -> bool:
        return self.dic is not None

    # ------------------------------------------------------------ проверка
    def _relevant(self, w: str) -> bool:
        """Слова другого алфавита не проверяем (английские имена в русском тексте)."""
        if self.lang in ("ru", "uk"):
            return bool(_CYR.search(w))
        return bool(_LAT.search(w))

    def check(self, word: str) -> bool:
        if self.dic is None:
            return True
        w = strip_accents(word)
        lw = w.lower()
        if len(w) < 2 or not self._relevant(w) or lw in self.user_words or lw in self.extra:
            return True
        hit = self._cache.get(lw if w.islower() else w)
        if hit is not None:
            return hit
        if "-" in w:
            ok = all(self.check(p) for p in w.split("-") if p) or self._lookup(w)
        else:
            ok = self._lookup(w) or self._lookup(w.replace("ё", "е").replace("Ё", "Е"))
        self._cache[lw if w.islower() else w] = ok
        return ok

    def _lookup(self, w: str) -> bool:
        with self._lock:
            try:
                return bool(self.dic.lookup(w))
            except Exception:  # noqa: BLE001
                return True

    def suggest(self, word: str, n: int = 6) -> list[str]:
        if self.dic is None:
            return []
        with self._lock:
            try:
                out = []
                for s in self.dic.suggest(strip_accents(word)):
                    out.append(s)
                    if len(out) >= n:
                        break
                return out
            except Exception:  # noqa: BLE001
                return []

    def errors(self, text: str) -> list[tuple[int, int, str]]:
        """[(начало, длина, слово)] — слова с ошибками в тексте."""
        return [(m.start(), m.end() - m.start(), m.group()) for m in WORD_RE.finditer(text)
                if not self.check(m.group())]


def toggle_accent(text: str, pos: int) -> tuple[str, int]:
    """Поставить/снять ударение на гласной перед позицией курсора (или на ближайшей слева
    гласной слова). Возвращает текст и новую позицию курсора."""
    vowels = "аеёиоуыэюяАЕЁИОУЫЭЮЯaeiouyAEIOUY"
    i = pos
    if i < len(text) and text[i] == ACCENT:
        i += 1
    if i > 0 and text[i - 1] == ACCENT:                    # уже стоит — снять
        return text[:i - 1] + text[i:], i - 1
    j = i - 1
    while j >= 0 and text[j] not in vowels and (text[j].isalpha() or text[j] == ACCENT):
        j -= 1
    if j < 0 or text[j] not in vowels:
        return text, pos
    if j + 1 < len(text) and text[j + 1] == ACCENT:
        return text[:j + 1] + text[j + 2:], max(j + 1, pos - 1)
    return text[:j + 1] + ACCENT + text[j + 1:], pos + 1
