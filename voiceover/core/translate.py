"""Машинный перевод реплик: DeepL, Google Cloud Translation (по ключу пользователя)
или локальная модель NLLB-200.

- реплики отправляются пачками, а не по одной;
- ответ «слишком много запросов» (429) приводит к ожиданию с нарастающей паузой;
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


def engines() -> list[str]:
    """Доступные переводчики. Локальная NLLB — только вне сборки для магазинов:
    лицензия модели (CC-BY-NC 4.0) не разрешает коммерческое использование."""
    from ..edition import is_store
    return (["deepl", "gcloud"] if is_store() else ["local", "deepl", "gcloud"])


def default_engine(saved: str) -> str:
    avail = engines()
    if saved in avail:
        return saved
    if "local" in avail and nllb_available()[0]:
        return "local"
    return "deepl"


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
        # Пакетные движки (локальная модель, DeepL, Google Cloud) получают реплики списком, без склейки
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
    try:  # без консоли полоса прогресса не может писать в stderr
        from huggingface_hub.utils import disable_progress_bars
        disable_progress_bars()
    except Exception:  # noqa: BLE001
        pass
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


VI_CHARS = set("ăâđêôơưĂÂĐÊÔƠƯ")


def detect_lang(text: str) -> str:
    """Грубое определение языка по письменности (для источника «авто» в NLLB).
    Латиница различается только для вьетнамского (по его особым буквам); остальные
    языки на латинице считаются английским — для них лучше выбрать язык явно."""
    if any(ch in VI_CHARS or 0x1EA0 <= ord(ch) <= 0x1EF9 for ch in text):
        return "vi"
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
        self._dir, self._threads = str(d), threads
        from .gpu import cuda_usable
        if device == "cuda" or (device == "auto" and cuda_usable()):
            try:
                self.model = ctranslate2.Translator(str(d), device="cuda",
                                                    compute_type="int8_float16")
            except Exception:  # noqa: BLE001  нет CUDA/cuDNN — на процессоре
                self.model = None
        if self.model is None:
            self._use_cpu()

    def _use_cpu(self):
        import ctranslate2
        self.model = ctranslate2.Translator(self._dir, device="cpu", compute_type="int8",
                                            intra_threads=self._threads)

    def _src_code(self, text: str) -> str:
        lang = self.source if self.source in NLLB_CODES else detect_lang(text)
        return NLLB_CODES.get(lang, "eng_Latn")

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        tgt = NLLB_CODES[self.target]
        sources = [[self._src_code(t)] + self.sp.encode(t, out_type=str) + ["</s>"] for t in texts]
        def run():
            return self.model.translate_batch(sources, target_prefix=[[tgt]] * len(texts),
                                              beam_size=4, max_decoding_length=256,
                                              max_batch_size=self.batch_size)
        try:
            res = run()
        except Exception as e:  # noqa: BLE001
            from .gpu import is_cuda_error
            if not is_cuda_error(e):
                raise
            self._use_cpu()          # библиотеки CUDA не загрузились — переводим на процессоре
            res = run()
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
        free = "https://api-free.deepl.com/v2/translate"
        pro = "https://api.deepl.com/v2/translate"
        # Ключи бесплатного тарифа обычно оканчиваются на «:fx»; если сервер не тот,
        # при ответе 403 пробуется второй
        self.url, self.alt_url = (free, pro) if self.key.endswith(":fx") else (pro, free)
        self._switched = False
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
                # Ключ мог быть от другого сервера (бесплатный/платный): пробуем второй один раз
                if not self._switched:
                    self._switched = True
                    self.url = self.alt_url
                    continue
                raise RuntimeError("DeepL: неверный ключ API (проверьте, что скопирован ключ "
                                   "со страницы «Ключи API» целиком)")
            if r.status_code == 456:
                raise RuntimeError("DeepL: исчерпан месячный лимит символов")
            if r.status_code >= 400:
                raise RuntimeError(f"DeepL ответил ошибкой {r.status_code}")
            return [x["text"] for x in r.json()["translations"]]
        raise RateLimited("DeepL временно ограничил число запросов, попробуйте позже")

    def translate(self, text: str) -> str:
        return self.translate_batch([text])[0]


# ============================================================================ Google Cloud

GCLOUD_URL = "https://translation.googleapis.com/language/translate/v2"


class GoogleCloudApi:
    """Официальный Google Cloud Translation (Basic, v2) по ключу API пользователя."""

    batch_size = 64          # до 128 строк в запросе

    def __init__(self, key: str, source: str, target: str, post: Optional[Callable] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 on_wait: Optional[Callable[[float], None]] = None):
        if not key.strip():
            raise RuntimeError("Не указан ключ Google Cloud Translation API")
        self.key = key.strip()
        self.source = None if source in ("auto", "", None) else source
        self.target = target
        self.sleep = sleep
        self.on_wait = on_wait
        if post is None:
            import requests
            post = requests.Session().post
        self.post = post

    def translate_batch(self, texts: list[str]) -> list[str]:
        body = {"q": texts, "target": self.target, "format": "text"}
        if self.source:
            body["source"] = self.source
        wait = 5.0
        for _ in range(6):
            r = self.post(GCLOUD_URL, params={"key": self.key}, json=body, timeout=60)
            if r.status_code in (429, 503):
                if self.on_wait:
                    self.on_wait(wait)
                self.sleep(wait)
                wait = min(wait * 2, 60.0)
                continue
            if r.status_code in (400, 401, 403):
                try:
                    msg = r.json()["error"]["message"]
                except Exception:  # noqa: BLE001
                    msg = f"ошибка {r.status_code}"
                raise RuntimeError("Google Cloud Translation: " + msg + "\n\nПроверьте ключ и что "
                                   "в проекте Google Cloud включён Cloud Translation API.")
            if r.status_code >= 400:
                raise RuntimeError(f"Google Cloud Translation ответил ошибкой {r.status_code}")
            import html
            return [html.unescape(x["translatedText"]) for x in r.json()["data"]["translations"]]
        raise RateLimited("Google Cloud Translation временно ограничил число запросов, "
                          "попробуйте позже")

    def translate(self, text: str) -> str:
        return self.translate_batch([text])[0]
