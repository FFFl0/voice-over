"""Пики громкости для отрисовки волны на таймлайне (считаются по кускам, кэшируются)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

PEAKS_PER_SEC = 50


def compute_peaks(wav: str, rate: int = PEAKS_PER_SEC, cache_dir: Optional[Path] = None,
                  cancel: Optional[Callable[[], bool]] = None) -> np.ndarray:
    """Максимум |x| в окнах длиной 1/rate с. Результат 0..1, float16."""
    if cache_dir is not None:
        key = f"peaks_{Path(wav).stem}_{int(os.path.getmtime(wav))}_{rate}.npy"
        cached = cache_dir / key
        if cached.exists():
            return np.load(cached)
    with sf.SoundFile(wav) as f:
        win = max(1, f.samplerate // rate)
        block = win * rate * 30   # по 30 секунд
        parts = []
        while True:
            if cancel and cancel():
                return np.zeros(0, np.float16)
            x = f.read(block, dtype="float32", always_2d=True)
            if not len(x):
                break
            m = np.abs(x).max(axis=1)
            n = len(m) // win
            if n:
                parts.append(m[: n * win].reshape(n, win).max(axis=1))
            if len(m) % win:
                parts.append(np.array([m[n * win:].max()]))
    peaks = np.concatenate(parts) if parts else np.zeros(0)
    peaks = np.clip(peaks, 0, 1).astype(np.float16)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(cache_dir / key, peaks)
    return peaks
