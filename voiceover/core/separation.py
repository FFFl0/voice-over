"""Отделение голосов от музыки и эффектов с помощью Demucs (нужен PyTorch).

Фильм обрабатывается кусками с перекрытием, чтобы не держать весь звук в памяти,
а куски склеиваются плавным перекрёстным переходом.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .ffmpeg import Cancelled

SEGMENT_SEC = 120.0
OVERLAP_SEC = 2.0


def demucs_available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
        import demucs  # noqa: F401
    except ImportError:
        return False, ("Для удаления оригинальных голосов нужны PyTorch и Demucs:\n"
                       "pip install torch demucs\n(см. README, раздел «Установка»)")
    return True, ""


def pick_device(pref: str = "auto") -> str:
    import torch
    if pref == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def process_in_chunks(in_wav: str, out_background: str, out_vocals: Optional[str],
                      vocals_fn: Callable[[np.ndarray, int], np.ndarray],
                      progress: Optional[Callable[[float, str], None]] = None,
                      cancel: Optional[Callable[[], bool]] = None,
                      segment_sec: float = SEGMENT_SEC, overlap_sec: float = OVERLAP_SEC) -> None:
    """Прогнать файл кусками через vocals_fn(x[n,2], sr) -> голоса[n,2].
    Фон = исходник − голоса. Соседние куски перекрываются и склеиваются кроссфейдом."""
    src = sf.SoundFile(in_wav)
    sr = src.samplerate
    total = src.frames
    seg = int(segment_sec * sr)
    ov = int(overlap_sec * sr)
    Path(out_background).parent.mkdir(parents=True, exist_ok=True)
    bg_part = out_background + ".part"
    vo_part = (out_vocals + ".part") if out_vocals else None
    bg_out = sf.SoundFile(bg_part, "w", sr, 2, "PCM_16", format="WAV")
    vo_out = sf.SoundFile(vo_part, "w", sr, 2, "PCM_16", format="WAV") if vo_part else None
    prev_bg_tail: Optional[np.ndarray] = None
    prev_vo_tail: Optional[np.ndarray] = None
    pos = 0
    try:
        while pos < total:
            if cancel and cancel():
                raise Cancelled()
            # первые `head` сэмплов — контекст (уже записаны), затем зона кроссфейда
            start = max(0, pos - ov)
            head = pos - start
            src.seek(start)
            x = src.read(min(total - start, seg + head), dtype="float32", always_2d=True)
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            x = x[:, :2]
            vocals = np.asarray(vocals_fn(x, sr), dtype=np.float32)[: len(x)]
            if len(vocals) < len(x):
                vocals = np.pad(vocals, ((0, len(x) - len(vocals)), (0, 0)))
            background = x - vocals  # всё, кроме голосов
            if prev_bg_tail is not None and len(prev_bg_tail):
                k = min(len(prev_bg_tail), len(x) - head)
                fade = np.linspace(0, 1, k, dtype=np.float32)[:, None]
                background[head:head + k] = prev_bg_tail[:k] * (1 - fade) + background[head:head + k] * fade
                vocals[head:head + k] = prev_vo_tail[:k] * (1 - fade) + vocals[head:head + k] * fade
            end_main = len(x) if start + len(x) >= total else len(x) - ov
            bg_out.write(np.clip(background[head:end_main], -1, 1))
            if vo_out:
                vo_out.write(np.clip(vocals[head:end_main], -1, 1))
            prev_bg_tail = background[end_main:]
            prev_vo_tail = vocals[end_main:]
            pos = start + end_main
            if progress:
                progress(pos / total, f"Отделение голосов: {pos / sr / 60:.1f} / {total / sr / 60:.1f} мин")
    except BaseException:
        bg_out.close()
        if vo_out:
            vo_out.close()
        src.close()
        Path(bg_part).unlink(missing_ok=True)
        if vo_part:
            Path(vo_part).unlink(missing_ok=True)
        raise
    bg_out.close()
    if vo_out:
        vo_out.close()
    src.close()
    Path(bg_part).replace(out_background)
    if out_vocals and vo_part:
        Path(vo_part).replace(out_vocals)


def separate_vocals(in_wav: str, out_background: str, out_vocals: Optional[str] = None,
                    model_name: str = "htdemucs", device: str = "auto",
                    progress: Optional[Callable[[float, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None) -> None:
    """Разделить звук на голоса и фон (музыка + эффекты) моделью Demucs."""
    ok, why = demucs_available()
    if not ok:
        raise RuntimeError(why)
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    if progress:
        progress(0.0, "Загрузка модели Demucs (при первом запуске скачивается)…")
    model = get_model(model_name)
    model.eval()
    dev = pick_device(device)
    model.to(dev)
    msr = model.samplerate
    vocals_idx = model.sources.index("vocals")

    def resample(x: np.ndarray, a: int, b: int) -> np.ndarray:
        if a == b:
            return x
        # julius (зависимость demucs, чистый PyTorch) вместо torchaudio: бинарная библиотека
        # torchaudio часто не загружается, если её версия не совпадает с версией PyTorch
        import julius
        t = torch.from_numpy(np.ascontiguousarray(x.T))
        return julius.resample_frac(t, a, b).numpy().T

    def vocals_fn(x: np.ndarray, sr: int) -> np.ndarray:
        wav = torch.from_numpy(np.ascontiguousarray(resample(x, sr, msr).T)).to(dev)
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        with torch.no_grad():
            out = apply_model(model, ((wav - mean) / std)[None], device=dev,
                              split=True, overlap=0.25, progress=False)[0]
        out = out * std + mean
        return resample(out[vocals_idx].cpu().numpy().T, msr, sr)

    process_in_chunks(in_wav, out_background, out_vocals, vocals_fn, progress, cancel)
