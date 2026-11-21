"""Ритмо-лента: когда звучит каждое слово реплики.

Если реплику распознал Whisper и текст с тех пор не меняли, берётся его тайминг слов.
Иначе слова раскладываются по длительности реплики пропорционально числу слогов,
а после знаков препинания оставляется короткая пауза — как говорят люди.
"""

from __future__ import annotations

import re

_VOWELS = set("аеёиоуыэюяaeiouyäöüéèêáíóúàìòùâîôûæøåıАЕЁИОУЫЭЮЯAEIOUY")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_PAUSE_AFTER = {",": 0.6, ";": 0.8, ":": 0.8, ".": 1.2, "!": 1.2, "?": 1.2, "…": 1.4, "—": 0.6}
_NOTE = re.compile(r"\([^)]*\)|\[[^\]]*\]")   # пометки для актёра: (смеётся), [шёпотом]


def syllables(word: str) -> int:
    cjk = len(_CJK.findall(word))
    if cjk:
        return cjk   # японский/китайский/корейский — по знакам
    n = sum(1 for ch in word if ch in _VOWELS)
    return max(1, n) if any(ch.isalnum() for ch in word) else 0


def spoken_text(text: str) -> str:
    """Текст без пометок в скобках — то, что действительно произносится."""
    return re.sub(r"\s+", " ", _NOTE.sub(" ", text)).strip()


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.lower())


def words_to_fractions(start: float, end: float, words: list) -> list:
    dur = max(1e-3, end - start)
    out = []
    for w in words or []:
        a, b, t = float(w[0]), float(w[1]), str(w[2]).strip()
        if t:
            out.append([round(min(1, max(0, (a - start) / dur)), 4),
                        round(min(1, max(0, (b - start) / dur)), 4), t])
    return out


def word_times(line) -> list[tuple[float, float, str]]:
    """[(начало, конец, слово)] в секундах фильма."""
    dur = max(0.05, line.end - line.start)
    words = getattr(line, "words", None) or []
    text = spoken_text(line.text)
    if words and _norm("".join(w[2] for w in words)) == _norm(text):
        return [(line.start + w[0] * dur, line.start + max(w[0], w[1]) * dur, w[2])
                for w in words]
    tokens = text.split()
    if not tokens:
        return []
    if len(tokens) == 1 and _CJK.search(tokens[0]) and len(tokens[0]) > 1:
        tokens = list(tokens[0])   # японский без пробелов — по знакам
    weights, pauses = [], []
    for tok in tokens:
        weights.append(max(0.5, float(syllables(tok))))
        pauses.append(_PAUSE_AFTER.get(tok[-1], 0.0) if tok else 0.0)
    pauses[-1] = 0.0
    total = sum(weights) + sum(pauses)
    k = dur / total
    out, t = [], line.start
    for tok, w, p in zip(tokens, weights, pauses):
        out.append((t, t + w * k, tok))
        t += (w + p) * k
    return out


# Темп речи, слогов в секунду: до RATE_OK — спокойно, до RATE_FAST — быстро, но реально,
# выше — актёр не успеет (перевод нужно сократить или растянуть реплику)
RATE_OK = 6.5
RATE_FAST = 8.0


def speech_rate(line) -> float:
    text = spoken_text(line.text)
    if not text:
        return 0.0
    n = sum(syllables(w) for w in text.split())
    if len(text.split()) == 1 and _CJK.search(text):
        n = len(_CJK.findall(text))
    return n / max(0.2, line.end - line.start)


def rate_level(rate: float) -> str:
    """ok | fast | too_fast"""
    return "too_fast" if rate > RATE_FAST else ("fast" if rate > RATE_OK else "ok")
