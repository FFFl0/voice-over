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
    batch = getattr(translator, "translate_batch", None)
    if batch is not None:
        # Пакетные движки (локальная модель, DeepL) получают реплики списком, без склейки
        size = getattr(translator, "batch_size", 16)
        groups = [list(range(k, min(k + size, len(todo)))) for k in range(0, len(todo), size)]
    for g in groups:
        if cancel and cancel():
            raise PartialTranslation("Перевод отменён", result, done)
        idx = [todo[k] for k in g]
        try:
            if batch is not None:
                out = [o.replace("\n", " ").strip() for o in batch([clean[i] for i in idx])]
            else:
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


# ============================================================================ локальный перевод
# NLLB-200 (Meta) через CTranslate2 — тот же движок, что у Whisper, PyTorch не нужен.
# Модель скачивается один раз с HuggingFace (лицензия CC-BY-NC 4.0 — для некоммерческого
# использования). Перевод напрямую между 200 языками, без интернета и без лимитов.

NLLB_CODES = {
    "ru": "rus_Cyrl", "en": "eng_Latn", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "zh-CN": "zho_Hans", "zh-TW": "zho_Hant", "uk": "ukr_Cyrl", "be": "bel_Cyrl",
    "kk": "kaz_Cyrl", "de": "deu_Latn", "fr": "fra_Latn", "es": "spa_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "pl": "pol_Latn", "tr": "tur_Latn",
    "ar": "arb_Arab", "hi": "hin_Deva", "th": "tha_Thai", "vi": "vie_Latn",
    "id": "ind_Latn",
}

# Готовые сборки CTranslate2 (int8); пробуются по очереди
NLLB_MODELS = {
    "600M": ("JustFrederik/nllb-200-distilled-600M-ct2-int8",
             "skywood/nllb-200-distilled-600M-ct2-int8"),
    "1.3B": ("OpenNMT/nllb-200-distilled-1.3B-ct2-int8",
             "JustFrederik/nllb-200-distilled-1.3B-ct2-int8",
             "winstxnhdw/nllb-200-distilled-1.3B-ct2-int8"),
}
NLLB_SIZES = [("600M", "Быстрая, ~600 МБ"), ("1.3B", "Точнее, ~1.4 ГБ, медленнее")]
NLLB_TOKENIZER_REPO = "facebook/nllb-200-distilled-600M"   # общий токенизатор всех размеров


def nllb_available() -> tuple[bool, str]:
    missing = []
    for mod, pkg in (("ctranslate2", "ctranslate2"), ("sentencepiece", "sentencepiece"),
                     ("huggingface_hub", "huggingface_hub")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        return False, ("Для локального перевода нужны пакеты: " + ", ".join(missing) +
                       "\npip install " + " ".join(missing))
    return True, ""


def nllb_dir(size: str):
    from .settings import data_dir
    return data_dir() / "models" / f"nllb-{size}"


def nllb_downloaded(size: str) -> bool:
    d = nllb_dir(size)
    return (d / "model.bin").exists() and (d / "sentencepiece.bpe.model").exists()


def ensure_nllb(size: str, progress: Optional[Callable[[str], None]] = None):
    """Скачать модель (один раз). Возвращает папку модели."""
    from huggingface_hub import hf_hub_download, snapshot_download
    d = nllb_dir(size)
    d.mkdir(parents=True, exist_ok=True)
    errors = []
    if not (d / "model.bin").exists():
        for repo in NLLB_MODELS.get(size, NLLB_MODELS["600M"]):
            if progress:
                progress(f"Скачивание модели перевода NLLB {size} ({repo}), это делается один раз…")
            try:
                snapshot_download(repo_id=repo, local_dir=str(d),
                                  allow_patterns=["*.bin", "*.json", "*.model", "*.txt"])
                if (d / "model.bin").exists():
                    break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{repo}: {e}")
        if not (d / "model.bin").exists():
            raise RuntimeError("Не удалось скачать модель перевода:\n" + "\n".join(errors))
    if not (d / "sentencepiece.bpe.model").exists():
        if progress:
            progress("Скачивание словаря модели перевода…")
        hf_hub_download(NLLB_TOKENIZER_REPO, "sentencepiece.bpe.model", local_dir=str(d))
    return d


def detect_lang(text: str) -> str:
    """Грубое определение языка по письменности (для источника «авто» в NLLB)."""
    kana = han = hangul = cyr = latin = 0
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:
            kana += 1
        elif 0x4E00 <= o <= 0x9FFF:
            han += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            hangul += 1
        elif 0x0400 <= o <= 0x04FF:
            cyr += 1
        elif ch.isalpha() and o < 0x250:
            latin += 1
    if kana:
        return "ja"
    if hangul >= max(han, 1):
        return "ko"
    if han:
        return "zh-CN"
    if cyr > latin:
        return "uk" if any(c in text for c in "їієґЇІЄҐ") else "ru"
    return "en"


class NllbLocal:
    """Локальный переводчик NLLB-200 (CTranslate2 + SentencePiece)."""

    batch_size = 16

    def __init__(self, source: str, target: str, size: str = "600M", device: str = "auto",
                 progress: Optional[Callable[[str], None]] = None):
        import ctranslate2
        import sentencepiece as spm
        if target not in NLLB_CODES:
            raise RuntimeError(f"Язык перевода «{target}» не поддерживается локальной моделью")
        self.source = source
        self.target = target
        d = ensure_nllb(size, progress)
        if progress:
            progress("Загрузка модели перевода в память…")
        self.sp = spm.SentencePieceProcessor(model_file=str(d / "sentencepiece.bpe.model"))
        import os
        threads = max(1, (os.cpu_count() or 4) // 2)
        self.model = None
        if device in ("auto", "cuda"):
            try:
                if ctranslate2.get_cuda_device_count() > 0:
                    self.model = ctranslate2.Translator(str(d), device="cuda",
                                                        compute_type="int8_float16")
            except Exception:  # noqa: BLE001  нет CUDA/cuDNN — на процессоре
                self.model = None
        if self.model is None:
            self.model = ctranslate2.Translator(str(d), device="cpu", compute_type="int8",
                                                intra_threads=threads)

    def _src_code(self, text: str) -> str:
        lang = self.source if self.source in NLLB_CODES else detect_lang(text)
        return NLLB_CODES.get(lang, "eng_Latn")

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        tgt = NLLB_CODES[self.target]
        sources = [[self._src_code(t)] + self.sp.encode(t, out_type=str) + ["</s>"] for t in texts]
        res = self.model.translate_batch(sources, target_prefix=[[tgt]] * len(texts),
                                         beam_size=4, max_decoding_length=256,
                                         max_batch_size=self.batch_size)
        out = []
        for r in res:
            toks = [t for t in r.hypotheses[0] if t != tgt]
            out.append(self.sp.decode(toks).strip())
        return out

    def translate(self, text: str) -> str:
        return self.translate_batch([text])[0]


# ============================================================================ DeepL

DEEPL_TARGET = {"ru": "RU", "en": "EN-US", "ja": "JA", "ko": "KO", "zh-CN": "ZH-HANS",
                "zh-TW": "ZH-HANT", "uk": "UK", "de": "DE", "fr": "FR", "es": "ES",
                "it": "IT", "pt": "PT-PT", "pl": "PL", "tr": "TR", "ar": "AR", "id": "ID"}


class DeepLApi:
    """DeepL API (бесплатный ключ: 500 000 символов в месяц)."""

    batch_size = 40

    def __init__(self, key: str, source: str, target: str, post: Optional[Callable] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 on_wait: Optional[Callable[[float], None]] = None):
        if not key.strip():
            raise RuntimeError("Не указан ключ DeepL (получить бесплатно: deepl.com/pro-api)")
        if target not in DEEPL_TARGET:
            raise RuntimeError(f"DeepL не переводит на язык «{target}»")
        self.key = key.strip()
        self.url = ("https://api-free.deepl.com/v2/translate" if self.key.endswith(":fx")
                    else "https://api.deepl.com/v2/translate")
        self.source = None if source in ("auto", "", None) else source.split("-")[0].upper()
        self.target = DEEPL_TARGET[target]
        self.sleep = sleep
        self.on_wait = on_wait
        if post is None:
            import requests
            post = requests.Session().post
        self.post = post

    def translate_batch(self, texts: list[str]) -> list[str]:
        data = [("text", t) for t in texts] + [("target_lang", self.target)]
        if self.source:
            data.append(("source_lang", self.source))
        wait = 5.0
        for _ in range(6):
            r = self.post(self.url, data=data, timeout=60,
                          headers={"Authorization": f"DeepL-Auth-Key {self.key}"})
            if r.status_code == 429:
                if self.on_wait:
                    self.on_wait(wait)
                self.sleep(wait)
                wait = min(wait * 2, 60.0)
                continue
            if r.status_code == 403:
                raise RuntimeError("DeepL: неверный ключ API")
            if r.status_code == 456:
                raise RuntimeError("DeepL: исчерпан месячный лимит символов")
            if r.status_code >= 400:
                raise RuntimeError(f"DeepL ответил ошибкой {r.status_code}")
            return [x["text"] for x in r.json()["translations"]]
        raise RateLimited("DeepL временно ограничил число запросов, попробуйте позже")

    def translate(self, text: str) -> str:
        return self.translate_batch([text])[0]
