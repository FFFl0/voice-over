"""Машинный перевод реплик (Google Translate через deep-translator, без ключа).

Реплики отправляются пачками: несколько строк склеиваются через перевод строки,
чтобы для фильма на тысячу реплик понадобились десятки запросов, а не тысяча.
Если переводчик вернул другое число строк, пачка переводится построчно.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

from .ffmpeg import Cancelled

# (код для Google, название)
LANGUAGES = [
    ("auto", "Определить автоматически"),
    ("ru", "Русский"), ("en", "Английский"), ("ja", "Японский"), ("ko", "Корейский"),
    ("zh-CN", "Китайский (упрощ.)"), ("zh-TW", "Китайский (трад.)"), ("uk", "Украинский"),
    ("be", "Белорусский"), ("kk", "Казахский"), ("de", "Немецкий"), ("fr", "Французский"),
    ("es", "Испанский"), ("it", "Итальянский"), ("pt", "Португальский"), ("pl", "Польский"),
    ("tr", "Турецкий"), ("ar", "Арабский"), ("hi", "Хинди"), ("th", "Тайский"),
    ("vi", "Вьетнамский"), ("id", "Индонезийский"),
]

# Коды Whisper -> коды Google (где отличаются)
WHISPER_TO_GOOGLE = {"zh": "zh-CN"}

CHUNK_CHARS = 4000


class Translator(Protocol):
    def translate(self, text: str) -> str: ...


def translator_available() -> tuple[bool, str]:
    try:
        import deep_translator  # noqa: F401
    except ImportError:
        return False, "Для перевода нужен пакет deep-translator:\npip install deep-translator"
    return True, ""


def google_translator(source: str, target: str) -> Translator:
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source=source or "auto", target=target)


def _chunks(texts: list[str], limit: int = CHUNK_CHARS) -> list[list[int]]:
    """Индексы строк, сгруппированные в пачки не длиннее limit символов."""
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, t in enumerate(texts):
        n = len(t) + 1
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _call(tr: Translator, text: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            res = tr.translate(text)
            return res if isinstance(res, str) else str(res or "")
        except Exception as e:  # noqa: BLE001  сеть, лимиты
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Ошибка перевода: {last}")


def translate_texts(texts: list[str], translator: Translator,
                    progress: Optional[Callable[[float, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None,
                    chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Перевести список строк, сохраняя их количество и порядок."""
    # Переносы строк внутри реплики заменяем пробелом: перевод строки — разделитель пачки
    clean = [" ".join(t.split()) for t in texts]
    result = list(clean)
    todo = [i for i, t in enumerate(clean) if t]
    groups = _chunks([clean[i] for i in todo], chunk_chars)
    done = 0
    for g in groups:
        if cancel and cancel():
            raise Cancelled()
        idx = [todo[k] for k in g]
        joined = "\n".join(clean[i] for i in idx)
        out = _call(translator, joined).split("\n")
        out = [o.strip() for o in out]
        if len(out) != len(idx):          # переводчик склеил или разбил строки
            out = [_call(translator, clean[i]).strip() for i in idx]
        for i, o in zip(idx, out):
            result[i] = o or clean[i]
        done += len(idx)
        if progress:
            progress(done / max(1, len(todo)), f"Перевод: {done} из {len(todo)} реплик")
    return result
