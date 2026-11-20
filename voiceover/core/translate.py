"""Машинный перевод реплик через бесплатный Google Переводчик (без ключа).

Как устроено, чтобы Google не блокировал по частоте запросов:
- реплики отправляются пачками до ~4000 символов, склеенными переводом строки, —
  на фильм в 400 реплик уходит несколько запросов, а не сотни;
- запрос — POST к JSON-интерфейсу translate.googleapis.com: он сохраняет переводы
  строк, поэтому пачка возвращается с тем же числом строк;
- между запросами выдерживается пауза, а ответ «слишком много запросов» (429)
  приводит к ожиданию с нарастающей паузой, а не к ошибке;
- если число строк всё же не совпало, пачка делится пополам, а не на отдельные строки;
- если перевод прервался, уже переведённые реплики возвращаются (PartialTranslation).
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

CHUNK_CHARS = 4000
GTX_URL = "https://translate.googleapis.com/translate_a/single"


class Translator(Protocol):
    def translate(self, text: str) -> str: ...


class RateLimited(RuntimeError):
    pass


class PartialTranslation(RuntimeError):
    """Перевод прервался: result — строки (переведённые там, где done[i] истинно)."""

    def __init__(self, message: str, result: list[str], done: list[bool]):
        super().__init__(message)
        self.result = result
        self.done = done


def translator_available() -> tuple[bool, str]:
    try:
        import requests  # noqa: F401  (проверка, что пакет установлен)
    except ImportError:
        return False, "Для перевода нужен пакет requests:\npip install requests"
    return True, ""


def parse_gtx(data) -> str:
    """Ответ translate_a/single: [[["перевод", "оригинал", ...], ...], ...]."""
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise RuntimeError("Неожиданный ответ переводчика")
    return "".join(seg[0] for seg in data[0] if isinstance(seg, list) and seg and seg[0])


class GoogleFree:
    """Бесплатный Google Переводчик (тот же интерфейс, что у браузерных расширений)."""

    def __init__(self, source: str, target: str, min_interval: float = 0.6,
                 max_waits: int = 6, post: Optional[Callable] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 on_wait: Optional[Callable[[float], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None):
        self.source = source or "auto"
        self.target = target
        self.min_interval = min_interval
        self.max_waits = max_waits
        self.sleep = sleep
        self.on_wait = on_wait
        self.cancel = cancel
        self._last = 0.0
        if post is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            post = session.post
        self.post = post

    def _pause(self, seconds: float) -> None:
        """Пауза короткими шагами, чтобы её можно было прервать кнопкой «Отмена»."""
        left = seconds
        while left > 0:
            if self.cancel and self.cancel():
                raise Cancelled()
            step = min(0.25, left)
            self.sleep(step)
            left -= step

    def translate(self, text: str) -> str:
        if not text.strip():
            return text
        wait = 5.0
        for attempt in range(self.max_waits + 1):
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                self._pause(gap)
            self._last = time.monotonic()
            try:
                r = self.post(GTX_URL, params={"client": "gtx", "sl": self.source,
                                               "tl": self.target, "dt": "t"},
                              data={"q": text}, timeout=30)
            except Exception as e:  # noqa: BLE001  сеть
                if attempt >= 2:
                    raise RuntimeError(f"Нет связи с Google Переводчиком: {e}") from e
                self._pause(3.0)
                continue
            if r.status_code in (429, 503):       # «слишком много запросов» — ждём
                if attempt >= self.max_waits:
                    break
                if self.on_wait:
                    self.on_wait(wait)
                self._pause(wait)
                wait = min(wait * 2, 120.0)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Google Переводчик ответил ошибкой {r.status_code}")
            return parse_gtx(r.json())
        raise RateLimited("Google Переводчик временно ограничил число запросов. "
                          "Подождите несколько минут и запустите перевод снова — "
                          "уже переведённые реплики сохранены и будут пропущены.")


def google_translator(source: str, target: str, on_wait=None, cancel=None) -> Translator:
    return GoogleFree(source, target, on_wait=on_wait, cancel=cancel)


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


def _translate_group(tr: Translator, items: list[str]) -> list[str]:
    """Перевести пачку строк; если строки «съехали», делить пачку пополам."""
    if len(items) == 1:
        return [tr.translate(items[0]).replace("\n", " ").strip()]
    out = [o.strip() for o in tr.translate("\n".join(items)).split("\n")]
    if len(out) == len(items):
        return out
    mid = len(items) // 2
    return _translate_group(tr, items[:mid]) + _translate_group(tr, items[mid:])


def translate_texts(texts: list[str], translator: Translator,
                    progress: Optional[Callable[[float, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None,
                    chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Перевести список строк, сохраняя их количество и порядок.

    При сбое посреди работы выбрасывает PartialTranslation с уже готовыми строками."""
    # Переносы строк внутри реплики заменяем пробелом: перевод строки — разделитель пачки
    clean = [" ".join(t.split()) for t in texts]
    result = list(clean)
    done = [not t for t in clean]
    todo = [i for i, t in enumerate(clean) if t]
    groups = _chunks([clean[i] for i in todo], chunk_chars)
    count = 0
    for g in groups:
        if cancel and cancel():
            raise PartialTranslation("Перевод отменён", result, done)
        idx = [todo[k] for k in g]
        try:
            out = _translate_group(translator, [clean[i] for i in idx])
        except Cancelled:
            raise PartialTranslation("Перевод отменён", result, done)
        except Exception as e:  # noqa: BLE001
            raise PartialTranslation(str(e), result, done) from e
        for i, o in zip(idx, out):
            result[i] = o or clean[i]
            done[i] = True
        count += len(idx)
        if progress:
            progress(count / max(1, len(todo)), f"Перевод: {count} из {len(todo)} реплик")
    return result
