"""Сравнение A/B: голос оригинала под репликой, пауза, затем дубль так, как он прозвучит в
сведении (с обрезкой, фейдами и эффектом роли). Оба фрагмента берутся из одного окна
времени и выравниваются по громкости, чтобы сравнивать интонацию и темп, а не уровень."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import mixer
from .models import Line, Project

PAD = 0.3
GAP = 0.6


def _active_rms(x: np.ndarray) -> float:
    if not len(x):
        return 0.0
    n = 480
    m = len(x) // n
    if m == 0:
        return float(np.sqrt(np.mean(x ** 2)))
    e = np.sqrt(np.mean(x[: m * n].reshape(m, n) ** 2, axis=1))
    loud = e[e >= max(1e-5, e.max() * 0.1)]
    return float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0


def build_ab(project: Project, line: Line, out_wav: str) -> tuple[float, float]:
    """Записать A/B в out_wav. Возвращает длительности частей A и B (сек)."""
    sr = project.mix.sample_rate
    clips, _w = mixer.prepare_clips(project, t_from=line.start, t_to=line.end)
    clip = next((c for c in clips if c.line_id == line.id), None)
    if clip is None:
        raise RuntimeError("У реплики нет записи")
    a = max(0.0, min(line.start, clip.start) - PAD)
    b = max(line.end, clip.end(sr)) + PAD
    # B — только эта запись, как в сведении
    tmp = Path(out_wav).with_suffix(".b.wav")
    mixer.render_mix(project, [clip], str(tmp), t_from=a, t_to=b, include_background=False)
    bb, _ = sf.read(str(tmp), dtype="float32", always_2d=True)
    tmp.unlink(missing_ok=True)
    take = bb.mean(axis=1)
    # A — голос оригинала (после отделения) или весь звук оригинала
    src = project.project_dir / "vocals.wav"
    if not src.exists():
        src = project.resolve(project.original_wav)
    if not src or not Path(src).exists():
        raise RuntimeError("Нет звука оригинала")
    with sf.SoundFile(str(src)) as f:
        if f.samplerate != sr:
            raise RuntimeError("Частота звука оригинала не совпадает с проектом")
        f.seek(min(f.frames, int(a * sr)))
        orig = f.read(len(take), dtype="float32", always_2d=True).mean(axis=1)
    orig = np.pad(orig, (0, len(take) - len(orig)))
    ra, rb = _active_rms(orig), _active_rms(take)
    target = max(ra, rb, 1e-4)
    if ra > 1e-5:
        orig = orig * min(16.0, target / ra)
    if rb > 1e-5:
        take = take * min(16.0, target / rb)
    out = np.concatenate([orig, np.zeros(int(GAP * sr), np.float32), take])
    peak = float(np.abs(out).max()) or 1.0
    if peak > 0.98:
        out *= 0.98 / peak
    sf.write(out_wav, np.stack([out, out], axis=1), sr)
    return len(orig) / sr, len(take) / sr
