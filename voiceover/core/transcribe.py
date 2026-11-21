"""Распознавание речи Whisper (faster-whisper): звук фильма -> реплики с таймингами."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .ffmpeg import Cancelled
from .subtitles import SubLine

MODELS = [
    ("large-v3-turbo", "large-v3-turbo — точно и быстро (рекомендуется, 1.6 ГБ)"),
    ("large-v3", "large-v3 — максимальная точность, медленно (3 ГБ)"),
    ("medium", "medium — хорошо (1.5 ГБ)"),
    ("small", "small — быстро, для слабых ПК (0.5 ГБ)"),
    ("base", "base — очень быстро, низкая точность"),
]

LANGUAGES = [
    (None, "Автоопределение"), ("ja", "Японский"), ("en", "Английский"), ("ru", "Русский"),
    ("ko", "Корейский"), ("zh", "Китайский"), ("uk", "Украинский"), ("de", "Немецкий"),
    ("fr", "Французский"), ("es", "Испанский"), ("it", "Итальянский"), ("pt", "Португальский"),
    ("pl", "Польский"), ("tr", "Турецкий"),
]

SENTENCE_END = tuple(".!?…。！？")
SOFT_BREAK = tuple(",;:—、，")

# Типичные «галлюцинации» Whisper на тишине и музыке
_HALLUCINATIONS = re.compile(
    r"(субтитр|редактор субтитров|продолжение следует|спасибо за просмотр|подписывайтесь|"
    r"thanks for watching|subscribe|ご視聴ありがとうございました|字幕)", re.I)


def whisper_available() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False, ("Для распознавания речи нужен пакет faster-whisper:\n"
                       "pip install faster-whisper")
    return True, ""


@dataclass
class Word:
    start: float
    end: float
    word: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: Optional[list[Word]] = None
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0


def is_garbage(text: str) -> bool:
    t = text.strip()
    if not t or not re.search(r"\w", t):
        return True
    return bool(_HALLUCINATIONS.search(t)) and len(t) < 80


def split_segment(seg: Segment, max_len: float = 7.0, min_len: float = 1.0,
                  max_gap: float = 0.8) -> list[SubLine]:
    """Разбить сегмент Whisper на реплики по концам предложений, паузам и длине."""
    words = [w for w in (seg.words or []) if w.word.strip()]
    if not words:
        return [SubLine(seg.start, seg.end, seg.text.strip())]
    out: list[SubLine] = []
    cur: list[Word] = []

    def flush(upto: int):
        nonlocal cur
        part, cur = cur[:upto], cur[upto:]
        if part:
            text = "".join(w.word for w in part).strip()
            out.append(SubLine(part[0].start, part[-1].end, text,
                               words=[[w.start, w.end, w.word.strip()] for w in part]))

    for w in words:
        if cur and w.start - cur[-1].end > max_gap:
            flush(len(cur))
        cur.append(w)
        dur = cur[-1].end - cur[0].start
        token = w.word.strip()
        if token.endswith(SENTENCE_END) and dur >= min_len:
            flush(len(cur))
        elif dur > max_len and len(cur) > 1:
            # рвём по последней запятой, если она есть, иначе перед текущим словом
            cut = next((i + 1 for i in range(len(cur) - 2, 0, -1)
                        if cur[i].word.strip().endswith(SOFT_BREAK)), len(cur) - 1)
            flush(cut)
    flush(len(cur))
    # склеиваем слишком короткие хвосты с предыдущей репликой, если они рядом
    merged: list[SubLine] = []
    for s in out:
        if merged and s.end - s.start < 0.4 and s.start - merged[-1].end < 0.3 \
                and not merged[-1].text.endswith(SENTENCE_END):
            merged[-1].end = s.end
            merged[-1].text = f"{merged[-1].text} {s.text}".strip()
        else:
            merged.append(s)
    return merged


def segments_to_lines(segments: Iterable[Segment], max_len: float = 7.0,
                      offset: float = 0.0) -> list[SubLine]:
    out: list[SubLine] = []
    for seg in segments:
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue
        for s in split_segment(seg, max_len):
            if is_garbage(s.text):
                continue
            s.start = round(s.start + offset, 3)
            s.end = round(max(s.end + offset, s.start + 0.3), 3)
            out.append(s)
    return out


def _load_model(model_name: str, device: str, download_root: str):
    from faster_whisper import WhisperModel
    from .gpu import cuda_usable
    if device == "cuda" or (device == "auto" and cuda_usable()):
        try:
            return WhisperModel(model_name, device="cuda", compute_type="float16",
                                download_root=download_root)
        except Exception:  # noqa: BLE001  нет CUDA/cuDNN — работаем на процессоре
            if device == "cuda":
                raise
    return WhisperModel(model_name, device="cpu", compute_type="int8",
                        download_root=download_root)


def transcribe(wav_path: str, model_name: str = "large-v3-turbo", language: Optional[str] = None,
               device: str = "auto", t_from: float = 0.0, t_to: Optional[float] = None,
               max_len: float = 7.0, download_root: str = "",
               progress: Optional[Callable[[float, str], None]] = None,
               cancel: Optional[Callable[[], bool]] = None,
               model=None) -> tuple[list[SubLine], str]:
    """Распознать речь. Возвращает реплики и определённый язык.
    model — готовая модель (для тестов), иначе загружается faster-whisper."""
    import numpy as np
    import soundfile as sf

    ok, why = whisper_available()
    if model is None and not ok:
        raise RuntimeError(why)
    if progress:
        progress(0.0, "Подготовка звука…")
    with sf.SoundFile(wav_path) as f:
        sr = f.samplerate
        a = int(t_from * sr)
        b = f.frames if t_to is None else min(f.frames, int(t_to * sr))
        f.seek(a)
        audio = f.read(max(0, b - a), dtype="float32", always_2d=True).mean(axis=1)
    if sr != 16000:  # Whisper работает на 16 кГц
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)),
                          audio).astype(np.float32) if len(audio) else audio
    total = len(audio) / 16000
    if cancel and cancel():
        raise Cancelled()
    if model is None:
        if progress:
            progress(0.0, f"Загрузка модели Whisper {model_name} (при первом запуске скачивается)…")
        model = _load_model(model_name, device, download_root or None)
    def run(m):
        seg_iter, info = m.transcribe(
            audio, language=language, task="transcribe", beam_size=5, word_timestamps=True,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False)
        segs: list[Segment] = []
        for s in seg_iter:
            if cancel and cancel():
                raise Cancelled()
            words = [Word(w.start, w.end, w.word) for w in (getattr(s, "words", None) or [])]
            segs.append(Segment(s.start, s.end, s.text, words,
                                getattr(s, "no_speech_prob", 0.0), getattr(s, "avg_logprob", 0.0)))
            if progress and total:
                progress(min(1.0, s.end / total),
                         f"Распознавание: {s.end / 60:.1f} / {total / 60:.1f} мин — {s.text.strip()[:40]}")
        return segs, info

    from .gpu import is_cuda_error
    try:
        segs, info = run(model)
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        # Видеокарта есть, но библиотек CUDA нет или они не подходят — повторяем на процессоре
        if device == "cuda" or not is_cuda_error(e) or model_name is None:
            raise
        if progress:
            progress(0.0, "Видеокарта недоступна (нет библиотек CUDA) — распознавание на процессоре…")
        model = _load_model(model_name, "cpu", download_root or None)
        segs, info = run(model)
    lang = getattr(info, "language", language or "")
    return segments_to_lines(segs, max_len, offset=t_from), lang
