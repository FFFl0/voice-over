"""«Слышать себя»: голос с микрофона в наушники с эффектом роли, в реальном времени.

Обработка идёт блоками по несколько миллисекунд, поэтому эффекты упрощены: полосовые
фильтры (FIR), эхо (линия задержки) и мягкое ограничение. Сдвиг тона (голос ниже/выше)
и компрессор в наушниках не слышны — они есть только в сведении."""

from __future__ import annotations

import numpy as np

# эффект → (нижняя частота, верхняя частота, усиление дБ, перегруз, [(задержка с, затухание)])
LIVE = {
    "": (0, 0, 0.0, 0.0, []),
    "radio": (400, 3000, 2.0, 3.0, []),
    "phone": (300, 3400, 1.0, 0.0, []),
    "megaphone": (700, 4500, -1.0, 4.0, []),
    "wall": (0, 700, -5.0, 0.0, []),
    "room": (0, 0, 0.0, 0.0, [(0.023, 0.22), (0.037, 0.15)]),
    "hall": (0, 0, 0.0, 0.0, [(0.070, 0.35), (0.130, 0.25), (0.210, 0.15)]),
    "cave": (0, 0, 0.0, 0.0, [(0.110, 0.45), (0.230, 0.32), (0.400, 0.2)]),
    "thoughts": (150, 0, -1.0, 0.0, [(0.045, 0.35)]),
}


def design_fir(sr: int, low: float, high: float, taps: int = 255) -> np.ndarray | None:
    """Окно Хэмминга × sinc: полоса low…high (0 — без границы)."""
    if not low and not high:
        return None
    n = np.arange(taps) - (taps - 1) / 2

    def lp(fc):
        return 2 * fc / sr * np.sinc(2 * fc / sr * n)

    if low and high:
        h = lp(high) - lp(low)
    elif high:
        h = lp(high)
    else:
        h = -lp(low)
        h[(taps - 1) // 2] += 1.0
    return (h * np.hamming(taps)).astype(np.float32)


class LiveFx:
    def __init__(self, effect: str = "", sr: int = 48000, gain_db: float = 0.0):
        lo, hi, g, drive, echoes = LIVE.get(effect or "", LIVE[""])
        self.sr = sr
        self.fir = design_fir(sr, lo, hi)
        self.gain = 10 ** ((g + gain_db) / 20)
        self.drive = drive
        self.echoes = [(int(d * sr), a) for d, a in echoes]
        self._tail = np.zeros(0 if self.fir is None else len(self.fir) - 1, np.float32)
        hist = max([d for d, _ in self.echoes] + [0])
        self._hist = np.zeros(hist, np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32).reshape(-1)
        y = x
        if self.fir is not None:                   # overlap-save: блоки без щелчков на стыках
            buf = np.concatenate([self._tail, x])
            y = np.convolve(buf, self.fir, mode="valid").astype(np.float32)
            self._tail = buf[len(buf) - len(self._tail):] if len(self._tail) else self._tail
        if self.echoes:
            full = np.concatenate([self._hist, y])
            out = y.copy()
            n, h = len(y), len(self._hist)
            for d, a in self.echoes:
                out += a * full[h - d:h - d + n]
            self._hist = full[-h:]
            y = out
        if self.drive:
            y = np.tanh(self.drive * y) / np.tanh(self.drive)
        y = y * self.gain
        return np.clip(y, -1.0, 1.0).astype(np.float32)
