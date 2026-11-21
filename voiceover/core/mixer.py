"""Сведение озвучки: подготовка реплик и потоковый микс с фоном/оригиналом."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from . import ffmpeg, fx
from .models import Line, MixMode, Project

ProgressCb = Callable[[float, str], None]
CancelCb = Callable[[], bool]

TARGET_RMS_DB = -20.0
SILENCE_DB = -45.0
DUB_PAD = 0.25    # запас вокруг реплики, где голос оригинала ещё удаляется, с
DUB_FADE = 0.2    # плавный переход фон ↔ оригинал, с
CHUNK_SEC = 20.0


def db_to_lin(db: float) -> float:
    return float(10 ** (db / 20.0))


def lin_to_db(x: float) -> float:
    return 20.0 * np.log10(max(x, 1e-9))


# ---------------------------------------------------------------- обработка клипов

def silence_bounds(x: np.ndarray, sr: int, threshold_db: float = SILENCE_DB,
                   pad: float = 0.03) -> tuple[int, int]:
    """Границы звучащей части [a, b) с небольшим запасом; (0, 0) — если тишина."""
    if x.size == 0:
        return 0, 0
    mono = np.abs(x if x.ndim == 1 else x.mean(axis=1))
    win = max(1, int(sr * 0.01))
    n = mono.size // win
    if n == 0:
        return 0, mono.size
    frames = mono[: n * win].reshape(n, win).max(axis=1)
    loud = np.nonzero(frames > db_to_lin(threshold_db))[0]
    if loud.size == 0:
        return 0, 0
    a = max(0, loud[0] * win - int(pad * sr))
    b = min(mono.size, (loud[-1] + 1) * win + int(pad * sr))
    return int(a), int(b)


def trim_silence(x: np.ndarray, sr: int, threshold_db: float = SILENCE_DB,
                 pad: float = 0.03) -> np.ndarray:
    """Обрезать тишину в начале и в конце (с небольшим запасом)."""
    a, b = silence_bounds(x, sr, threshold_db, pad)
    return x[a:b]


def active_rms(x: np.ndarray, sr: int) -> float:
    """RMS только по звучащим участкам (паузы не занижают громкость)."""
    if x.size == 0:
        return 0.0
    mono = x if x.ndim == 1 else x.mean(axis=1)
    win = max(1, int(sr * 0.05))
    n = mono.size // win
    if n == 0:
        return float(np.sqrt(np.mean(mono ** 2)))
    fr = mono[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(fr ** 2, axis=1))
    thr = db_to_lin(SILENCE_DB + 10)
    act = rms[rms > thr]
    if act.size == 0:
        return float(np.sqrt(np.mean(mono ** 2)))
    return float(np.sqrt(np.mean(act ** 2)))


def soft_limit(x: np.ndarray, threshold: float = 0.89) -> np.ndarray:
    """Мягкий лимитер: значения выше порога плавно сжимаются, не превышая 1.0."""
    a = np.abs(x)
    over = a > threshold
    if not over.any():
        return x
    y = x.copy()
    k = 1.0 - threshold
    y[over] = np.sign(x[over]) * (threshold + k * np.tanh((a[over] - threshold) / k))
    return y


@dataclass
class Clip:
    line_id: str
    path: str          # подготовленный mono float32 WAV
    start: float       # время начала в фильме, сек
    length: int        # длина в сэмплах
    gain: float        # итоговое линейное усиление

    def end(self, sr: int) -> float:
        return self.start + self.length / sr


def clip_source(project: Project, line: Line) -> tuple[Optional[Path], float]:
    """Файл записи реплики и его смещение относительно начала реплики (или None)."""
    if line.rec_path:
        p = project.resolve(line.rec_path)
        if p and p.exists():
            return p, line.rec_offset
    return None, 0.0


def _cache_key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:20]


def prepare_clips(project: Project, progress: Optional[ProgressCb] = None,
                  cancel: Optional[CancelCb] = None,
                  t_from: float = 0.0, t_to: float = float("inf")) -> tuple[list[Clip], list[str]]:
    """Подготовить реплики к сведению. Возвращает клипы и список предупреждений."""
    sr = project.mix.sample_rate
    cache = project.project_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    lines = sorted([ln for ln in project.lines if ln.enabled], key=lambda x: x.start)
    clips: list[Clip] = []
    warnings: list[str] = []
    todo = [i for i, ln in enumerate(lines) if ln.end >= t_from - 2 and ln.start <= t_to]
    # образец звука оригинала для подгонки тембра: голоса после отделения или весь оригинал
    ref_path = project.project_dir / "vocals.wav"
    if not ref_path.exists():
        ref_path = project.resolve(project.original_wav)
    if ref_path is not None and not ref_path.exists():
        ref_path = None
    for k, i in enumerate(todo):
        if cancel and cancel():
            raise ffmpeg.Cancelled()
        ln = lines[i]
        if progress:
            progress(k / max(1, len(todo)), f"Подготовка реплик {k + 1}/{len(todo)}")
        src, offset = clip_source(project, ln)
        if src is None:
            if ln.text.strip():
                warnings.append(f"{ln.start:8.2f}s: нет записи для реплики «{ln.text[:40]}»")
            continue
        ch = project.character(ln.character_id)
        mtime = os.path.getmtime(src)

        # приводим к mono float32 нужной частоты (запись не обрезается — сдвиг уже учтён),
        # затем шумоподавление, подгонка тембра и эффект роли
        dn = round(project.mix.denoise, 2)
        fx_chain = fx.effect_filter(ch.effect, ch.compress) if ch else ""
        meq = bool(ch and ch.match_eq and ref_path is not None)
        key = _cache_key(src, mtime, sr, True, dn, fx_chain,
                         (ref_path, round(ln.start, 2), round(ln.end, 2)) if meq else "")
        path = cache / f"b_{key}.wav"
        if not path.exists():
            tmp = path.with_suffix(".tmp.wav")
            ffmpeg.convert_audio(str(src), str(tmp), sr)
            data, _ = sf.read(str(tmp), dtype="float32")
            tmp.unlink(missing_ok=True)
            if data.ndim > 1:
                data = data.mean(axis=1)
            if dn > 0:
                data = fx.denoise(data, sr, dn)
            if meq:
                ref = fx.read_reference(ref_path, ln.start, ln.end, sr)
                if ref is not None:
                    data = fx.match_eq(data, ref, sr)
            if fx_chain:
                sf.write(str(tmp), data, sr, subtype="FLOAT")
                tmp2 = path.with_suffix(".fx.wav")
                ffmpeg.convert_audio(str(tmp), str(tmp2), sr, filters=fx_chain)
                tmp.unlink(missing_ok=True)
                data, _ = sf.read(str(tmp2), dtype="float32")
                tmp2.unlink(missing_ok=True)
            sf.write(str(path), data, sr, subtype="FLOAT")
        if sf.info(str(path)).frames == 0:
            continue
        data, _ = sf.read(str(path), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)

        # 3) громкость
        gain_db = ln.gain_db + project.mix.voices_db + (ch.volume_db if ch else 0.0)
        if project.mix.normalize_voices:
            r = active_rms(data, sr)
            if r > 0:
                gain_db += float(np.clip(TARGET_RMS_DB - lin_to_db(r), -12, 18))
        clips.append(Clip(ln.id, str(path), ln.start + offset, len(data), db_to_lin(gain_db)))
    if progress:
        progress(1.0, "Реплики подготовлены")
    return clips, warnings


# ---------------------------------------------------------------- микс

def _merge_intervals(iv: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    iv = sorted(iv)
    out: list[list[float]] = []
    for a, b in iv:
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def duck_envelope(t0: float, n: int, sr: int, intervals: list[tuple[float, float]],
                  attack: float, release: float) -> np.ndarray:
    """Огибающая 0..1 (1 — идёт реплика) для участка [t0, t0+n/sr)."""
    env = np.zeros(n, dtype=np.float32)
    t1 = t0 + n / sr
    tt = None
    for a, b in intervals:
        if b + release < t0 or a - attack > t1:
            continue
        if tt is None:
            tt = t0 + np.arange(n, dtype=np.float64) / sr
        e = np.ones(n, dtype=np.float32)
        pre = tt < a
        e[pre] = np.clip(1.0 - (a - tt[pre]) / max(attack, 1e-3), 0, 1)
        post = tt > b
        e[post] = np.clip(1.0 - (tt[post] - b) / max(release, 1e-3), 0, 1)
        np.maximum(env, e, out=env)
    return env


def stem_paths(project: Project) -> Optional[tuple[Path, Path]]:
    """Фон и голоса после «Отделить голоса», если оба файла на месте."""
    bg = project.resolve(project.background_wav)
    vo = project.project_dir / "vocals.wav"
    if bg is not None and bg.exists() and vo.exists():
        return bg, vo
    return None


def wave_keys(project: Project) -> list[str]:
    """Дорожки звука на таймлайне: после отделения — голоса и фон, иначе оригинал."""
    return ["vocals", "background"] if stem_paths(project) else ["original"]


def _read_block(f: sf.SoundFile, pos: int, n: int) -> np.ndarray:
    out = np.zeros((n, 2), np.float32)
    if pos < f.frames:
        f.seek(pos)
        d = f.read(n, dtype="float32", always_2d=True)
        if d.shape[1] == 1:
            d = np.repeat(d, 2, axis=1)
        out[: len(d)] = d[:, :2]
    return out


def _blend_original(orig_file: sf.SoundFile, bg: np.ndarray, pos: int, t0: float, sr: int,
                    speech: list[tuple[float, float]]) -> np.ndarray:
    """Фон без голосов под репликами, оригинал — вне их (с плавными переходами)."""
    env = duck_envelope(t0, len(bg), sr, speech, DUB_FADE, DUB_FADE)
    if env.min() >= 1.0:
        return bg
    o = np.zeros_like(bg)
    if pos < orig_file.frames:
        orig_file.seek(pos)
        d = orig_file.read(len(bg), dtype="float32", always_2d=True)
        if d.shape[1] == 1:
            d = np.repeat(d, 2, axis=1)
        o[: len(d)] = d[:, :2]
    e = env[:, None]
    return bg * e + o * (1.0 - e)


def background_path(project: Project) -> Optional[Path]:
    if project.mix.mode == MixMode.DUBBING:
        return project.resolve(project.background_wav)
    return project.resolve(project.original_wav)


def render_mix(project: Project, clips: list[Clip], out_wav: str,
               progress: Optional[ProgressCb] = None, cancel: Optional[CancelCb] = None,
               t_from: float = 0.0, t_to: Optional[float] = None,
               include_background: bool = True) -> None:
    """Потоковое сведение: фон (оригинал с приглушением или фон без голосов) + голоса."""
    sr = project.mix.sample_rate
    mix = project.mix
    bg_file: Optional[sf.SoundFile] = None
    orig_file: Optional[sf.SoundFile] = None   # дубляж: оригинал там, где нет реплик
    voc_file: Optional[sf.SoundFile] = None    # голоса оригинала (после отделения)
    muted = set(mix.muted)
    stems = stem_paths(project) if include_background else None
    if stems:
        # Голоса отделены: основа = фон + голоса оригинала, каждую дорожку можно заглушить.
        # Закадровый — голоса звучат (с приглушением под репликами), дубляж — убираются.
        bg_file = sf.SoundFile(str(stems[0]))
        voc_file = sf.SoundFile(str(stems[1]))
        for fl in (bg_file, voc_file):
            if fl.samplerate != sr:
                bg_file.close()
                voc_file.close()
                raise RuntimeError(f"Частота {fl.name} — {fl.samplerate} Гц, нужна {sr} Гц")
    elif include_background:
        bp = background_path(project)
        if bp is None or not bp.exists():
            if mix.mode == MixMode.DUBBING:
                raise RuntimeError("Для режима дубляжа сначала выполните «Отделить голоса» "
                                   "(получить фон без оригинальных голосов).")
            raise RuntimeError("Нет звука оригинала. Откройте видео заново или извлеките звук.")
        bg_file = sf.SoundFile(str(bp))
        if bg_file.samplerate != sr:
            raise RuntimeError(f"Частота фона {bg_file.samplerate} Гц не совпадает с {sr} Гц")
        if mix.mode == MixMode.DUBBING and mix.dub_only_lines:
            op = project.resolve(project.original_wav)
            if op is not None and op.exists():
                orig_file = sf.SoundFile(str(op))
                if orig_file.samplerate != sr:
                    orig_file.close()
                    orig_file = None

    total = project.duration
    if bg_file is not None:
        total = max(total, bg_file.frames / sr)
    if clips:
        total = max(total, max(c.end(sr) for c in clips))
    t_end = min(total, t_to) if t_to is not None else total
    t_from = max(0.0, t_from)

    intervals = _merge_intervals([(c.start, c.end(sr)) for c in clips], 0.3)
    # где звучал голос оригинала: все включённые реплики (и с записью, и без)
    speech = _merge_intervals(sorted(
        [(ln.start - DUB_PAD, ln.end + DUB_PAD) for ln in project.lines
         if ln.enabled and (ln.text.strip() or ln.rec_path)]
        + [(c.start, c.end(sr)) for c in clips]), 0.5)
    duck_lin = db_to_lin(mix.duck_db)
    bg_gain = db_to_lin(mix.background_db)
    clip_files: dict[str, sf.SoundFile] = {}

    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    chunk = int(CHUNK_SEC * sr)
    pos = int(t_from * sr)
    end = int(t_end * sr)
    try:
        with sf.SoundFile(out_wav, "w", samplerate=sr, channels=2, subtype="PCM_24") as out:
            while pos < end:
                if cancel and cancel():
                    raise ffmpeg.Cancelled()
                n = min(chunk, end - pos)
                t0 = pos / sr
                buf = np.zeros((n, 2), dtype=np.float32)
                bg = None
                if voc_file is not None and bg_file is not None:
                    bg = np.zeros((n, 2), np.float32)
                    if "background" not in muted:
                        bg += _read_block(bg_file, pos, n)
                    if "vocals" not in muted:
                        if mix.mode != MixMode.DUBBING:
                            bg += _read_block(voc_file, pos, n)
                        elif mix.dub_only_lines:
                            keep = 1.0 - duck_envelope(t0, n, sr, speech, DUB_FADE, DUB_FADE)
                            if keep.max() > 0:
                                bg += _read_block(voc_file, pos, n) * keep[:, None]
                elif bg_file is not None and pos < bg_file.frames:
                    bg = _read_block(bg_file, pos, n)
                    if orig_file is not None:
                        bg = _blend_original(orig_file, bg, pos, t0, sr, speech)
                    if muted & {"original", "background"}:
                        bg[:] = 0.0
                if bg is not None:
                    bg = bg * bg_gain
                    if mix.mode == MixMode.VOICEOVER and mix.duck_db < 0:
                        env = duck_envelope(t0, len(bg), sr, intervals,
                                            mix.duck_attack, mix.duck_release)
                        bg *= (1.0 - env * (1.0 - duck_lin))[:, None]
                    buf[: len(bg)] += bg
                for c in clips:
                    cs = int(round(c.start * sr))
                    ce = cs + c.length
                    if ce <= pos or cs >= pos + n:
                        continue
                    f = clip_files.get(c.path)
                    if f is None:
                        f = clip_files[c.path] = sf.SoundFile(c.path)
                    a = max(cs, pos)
                    b = min(ce, pos + n)
                    f.seek(a - cs)
                    d = f.read(b - a, dtype="float32", always_2d=True).mean(axis=1) * c.gain
                    # короткие фейды на краях клипа против щелчков
                    fade = min(len(d) // 2, int(0.005 * sr))
                    if fade > 0 and a == cs:
                        d[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
                    if fade > 0 and b == ce:
                        d[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
                    buf[a - pos: a - pos + len(d)] += d[:, None]
                out.write(soft_limit(buf))
                pos += n
                if progress:
                    progress((pos - t_from * sr) / max(1, end - t_from * sr), "Сведение")
    finally:
        if bg_file is not None:
            bg_file.close()
        if orig_file is not None:
            orig_file.close()
        if voc_file is not None:
            voc_file.close()
        for f in clip_files.values():
            f.close()


def render_project(project: Project, out_wav: str, progress: Optional[ProgressCb] = None,
                   cancel: Optional[CancelCb] = None, t_from: float = 0.0,
                   t_to: Optional[float] = None) -> list[str]:
    """Полный цикл: подготовка реплик + сведение. Возвращает предупреждения."""
    def p1(f, msg):
        if progress:
            progress(f * 0.3, msg)

    def p2(f, msg):
        if progress:
            progress(0.3 + f * 0.7, msg)

    clips, warnings = prepare_clips(project, p1, cancel, t_from,
                                    t_to if t_to is not None else float("inf"))
    render_mix(project, clips, out_wav, p2, cancel, t_from, t_to)
    return warnings
