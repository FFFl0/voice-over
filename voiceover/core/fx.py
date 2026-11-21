"""Обработка записей перед сведением: шумоподавление, эффекты роли, подгонка тембра."""

from __future__ import annotations

from typing import Optional

import numpy as np

# Эффекты персонажа: ключ → (название, фильтр FFmpeg). Пустой ключ — без обработки.
EFFECTS: dict[str, tuple[str, str]] = {
    "": ("Без эффекта", ""),
    "radio": ("Рация", "highpass=f=400,highpass=f=400,lowpass=f=3000,lowpass=f=3000,"
                       "acompressor=threshold=-24dB:ratio=8:attack=3:release=60:makeup=4,"
                       "acrusher=bits=12:mode=log:aa=1:mix=0.35"),
    "phone": ("Телефон", "highpass=f=300,highpass=f=300,lowpass=f=3400,lowpass=f=3400,"
                         "acompressor=threshold=-20dB:ratio=4:attack=5:release=80:makeup=3"),
    "megaphone": ("Мегафон", "highpass=f=700,highpass=f=700,lowpass=f=4500,lowpass=f=4500,"
                             "acrusher=bits=10:mode=log:aa=1:mix=0.5,volume=-2dB"),
    "wall": ("За стеной", "lowpass=f=700,lowpass=f=700,volume=-5dB"),
    "room": ("Комната", "aecho=0.8:0.55:23|37:0.22|0.15"),
    "hall": ("Большой зал", "aecho=0.8:0.6:70|130|210:0.35|0.25|0.15"),
    "cave": ("Пещера", "aecho=0.8:0.7:110|230|400:0.45|0.32|0.2"),
    "thoughts": ("Мысли", "highpass=f=150,aecho=0.8:0.6:45:0.35,volume=-1dB"),
    "low": ("Голос ниже (чудовище)", "asetrate=48000*0.82,aresample=48000,atempo=1.2195"),
    "high": ("Голос выше", "asetrate=48000*1.18,aresample=48000,atempo=0.8475"),
}

COMPRESS = "acompressor=threshold=-20dB:ratio=3:attack=8:release=150:makeup=2"


def effect_filter(effect: str, compress: bool) -> str:
    parts = []
    if compress:
        parts.append(COMPRESS)
    fx = EFFECTS.get(effect or "", ("", ""))[1]
    if fx:
        parts.append(fx)
    return ",".join(parts)


# ------------------------------------------------------------------ STFT
def _stft(x: np.ndarray, n: int, hop: int) -> np.ndarray:
    pad = np.concatenate([np.zeros(n // 2, x.dtype), x, np.zeros(n, x.dtype)])
    frames = 1 + (len(pad) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    win = np.hanning(n).astype(np.float32)
    return np.fft.rfft(pad[idx] * win, axis=1)


def _istft(spec: np.ndarray, n: int, hop: int, length: int) -> np.ndarray:
    win = np.hanning(n).astype(np.float32)
    frames = np.fft.irfft(spec, n=n, axis=1).astype(np.float32) * win
    out = np.zeros(hop * (len(frames) - 1) + n, np.float32)
    norm = np.zeros_like(out)
    for i, fr in enumerate(frames):
        out[i * hop:i * hop + n] += fr
        norm[i * hop:i * hop + n] += win * win
    out /= np.maximum(norm, 1e-6)
    return out[n // 2:n // 2 + length]


def denoise(x: np.ndarray, sr: int, strength: float = 0.5, highpass: float = 80.0) -> np.ndarray:
    """Шумоподавление спектральным вычитанием по профилю шума из самых тихих кадров
    записи, плюс срез низких частот (гул, удары по столу). strength 0..1."""
    if strength <= 0 or len(x) < sr // 10:
        return x
    n = 1024 if sr <= 24000 else 2048
    hop = n // 4
    spec = _stft(x.astype(np.float32), n, hop)
    mag = np.abs(spec)
    energy = mag.mean(axis=1)
    quiet = energy <= np.percentile(energy, 15)
    noise = np.median(mag[quiet], axis=0) if quiet.sum() >= 3 else np.percentile(mag, 10, axis=0)
    over = 1.0 + 2.5 * strength                    # насколько выше шума подавлять
    floor = 10 ** (-(6 + 24 * strength) / 20)      # не глубже −6…−30 дБ: без «бульканья»
    gain = np.clip(1.0 - (over * noise[None, :]) / np.maximum(mag, 1e-9), floor, 1.0)
    # сглаживание по времени и частоте убирает «музыкальный шум»
    k = np.ones(3) / 3
    gain = np.apply_along_axis(lambda g: np.convolve(g, k, mode="same"), 0, gain)
    gain = np.apply_along_axis(lambda g: np.convolve(g, k, mode="same"), 1, gain)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    if highpass > 0:
        gain *= np.clip((freqs - highpass * 0.7) / (highpass * 0.3), 0, 1)[None, :]
    return _istft(spec * gain, n, hop, len(x))


def _band_profile(x: np.ndarray, sr: int, n: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """Средний спектр (дБ) по третьоктавным полосам 100 Гц – 12 кГц, только по громким кадрам."""
    spec = np.abs(_stft(x.astype(np.float32), n, n // 2)) ** 2
    e = spec.sum(axis=1)
    loud = e >= np.percentile(e, 50) if len(e) > 4 else np.ones(len(e), bool)
    p = spec[loud].mean(axis=0) + 1e-12
    freqs = np.fft.rfftfreq(n, 1 / sr)
    centers = 100 * 2 ** (np.arange(0, 27) / 3)
    centers = centers[centers < min(12000, sr / 2 * 0.9)]
    db = []
    for c in centers:
        m = (freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))
        db.append(10 * np.log10(p[m].mean()) if m.any() else -120)
    return centers, np.array(db)


def match_eq(x: np.ndarray, ref: np.ndarray, sr: int, max_db: float = 6.0) -> np.ndarray:
    """Подогнать тембр записи к звуку оригинала (окраска микрофона и помещения): плавная
    коррекция по третьоктавам, не больше ±max_db, общая громкость не меняется."""
    if len(x) < sr // 4 or len(ref) < sr // 4:
        return x
    c, a = _band_profile(x, sr)
    _, b = _band_profile(ref, sr)
    diff = b - a
    diff -= np.median(diff)                         # громкость не трогаем, только тембр
    diff = np.convolve(np.pad(diff, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    diff = np.clip(diff, -max_db, max_db)
    n = 1 << int(np.ceil(np.log2(len(x) + 1)))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    curve = np.interp(np.log2(np.maximum(freqs, 1)), np.log2(c), diff, left=0.0, right=diff[-1])
    y = np.fft.irfft(np.fft.rfft(x, n) * 10 ** (curve / 20), n)[: len(x)]
    return y.astype(np.float32)


def read_reference(path, t0: float, t1: float, sr: int) -> Optional[np.ndarray]:
    import soundfile as sf
    try:
        with sf.SoundFile(str(path)) as f:
            if f.samplerate != sr:
                return None
            a, b = max(0, int(t0 * sr)), min(f.frames, int(t1 * sr))
            if b - a < sr // 4:
                return None
            f.seek(a)
            d = f.read(b - a, dtype="float32", always_2d=True)
    except Exception:  # noqa: BLE001
        return None
    return d.mean(axis=1)
