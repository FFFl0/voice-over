"""Автоподгонка записи к оригиналу: подбирается сдвиг, при котором громкостная огибающая
записи (слоги, паузы) лучше всего совпадает с голосом оригинала под репликой.

Сравнивается с «голосами» после отделения (точнее всего) или с оригиналом целиком.
Записи на другом языке тоже подгоняются — по началу фразы, паузам и ударным слогам."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import soundfile as sf

from .models import Line, Project

HOP = 0.01          # шаг огибающей, с
MAX_SHIFT = 0.6     # насколько далеко искать от текущего сдвига, с
MIN_SCORE = 0.3     # ниже — совпадение ненадёжно, сдвиг не меняем


@dataclass
class AlignResult:
    offset: float          # новый сдвиг записи
    shift: float           # насколько он изменился
    score: float           # 0..1, насколько уверенно
    applied: bool


def envelope(x: np.ndarray, sr: int, hop: float = HOP) -> np.ndarray:
    """Лог-энергия по кадрам, сглаженная и нормированная."""
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = max(1, int(round(sr * hop)))
    m = len(x) // n
    if m == 0:
        return np.zeros(0, np.float32)
    e = np.sqrt(np.mean(x[: m * n].reshape(m, n) ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(e)
    db = np.maximum(db, db.max() - 45)            # тишину ниже -45 дБ от пика не различаем
    k = np.ones(3) / 3
    db = np.convolve(db, k, mode="same")
    return (db - db.mean()).astype(np.float32)


def _read(path, t0: float, t1: float) -> tuple[np.ndarray, int]:
    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        a = max(0, int(t0 * sr))
        b = min(f.frames, int(t1 * sr))
        if b <= a:
            return np.zeros(0, np.float32), sr
        f.seek(a)
        return f.read(b - a, dtype="float32", always_2d=True), sr


def reference_path(project: Project):
    vo = project.project_dir / "vocals.wav"
    if vo.exists():
        return vo
    return project.resolve(project.original_wav)


def best_offset(take_env: np.ndarray, ref_env: np.ndarray, ref_t0: float, line_start: float,
                cur: float, max_shift: float = MAX_SHIFT) -> tuple[float, float]:
    """Сдвиг записи (от начала реплики) с наибольшей корреляцией и её значение."""
    best, best_o = -1.0, cur
    L = len(take_env)
    if L < 10:
        return cur, 0.0
    steps = int(round(max_shift / HOP))
    for k in range(-steps, steps + 1):
        o = cur + k * HOP
        i = int(round((line_start + o - ref_t0) / HOP))
        if i < 0 or i + L > len(ref_env):
            continue
        r = ref_env[i:i + L]
        a, b = take_env - take_env.mean(), r - r.mean()
        den = float(np.sqrt((a * a).sum() * (b * b).sum()))
        if den <= 0:
            continue
        c = float((a * b).sum() / den)
        # при равенстве — ближе к текущему сдвигу
        if c > best + 1e-6:
            best, best_o = c, o
    return best_o, max(0.0, best)


def align_line(project: Project, line: Line, max_shift: float = MAX_SHIFT,
               min_score: float = MIN_SCORE) -> Optional[AlignResult]:
    """Подогнать сдвиг записи реплики. None — нет записи или звука оригинала."""
    rec = project.resolve(line.rec_path)
    ref = reference_path(project)
    if not rec or not rec.exists() or not ref or not ref.exists():
        return None
    take, sr_t = sf.read(str(rec), dtype="float32", always_2d=True)
    if len(take) / sr_t > 30:
        take = take[: 30 * sr_t]
    dur = len(take) / sr_t
    t0 = max(0.0, line.start + line.rec_offset - max_shift - 0.1)
    t1 = line.start + line.rec_offset + dur + max_shift + 0.1
    ref_audio, sr_r = _read(ref, t0, t1)
    te, re_ = envelope(take, sr_t), envelope(ref_audio, sr_r)
    o, score = best_offset(te, re_, t0, line.start, line.rec_offset, max_shift)
    applied = score >= min_score and abs(o - line.rec_offset) >= HOP / 2
    res = AlignResult(round(o, 3), round(o - line.rec_offset, 3), round(score, 2), applied)
    if applied:
        line.rec_offset = res.offset
        line.takes[line.rec_path] = res.offset
    return res
