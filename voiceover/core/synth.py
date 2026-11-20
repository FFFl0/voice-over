"""Пакетный синтез реплик. Не меняет проект напрямую — результаты отдаются через колбэк,
чтобы интерфейс применял их в главном потоке."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..tts import get_engine
from .models import Line, LineSource, Project, tts_hash


@dataclass
class SynthResult:
    line_id: str
    path: str = ""       # относительный путь в папке проекта
    hash: str = ""
    error: str = ""


def lines_needing_synth(project: Project, lines: list[Line], force: bool = False) -> list[Line]:
    out = []
    for ln in lines:
        ch = project.character(ln.character_id)
        if not ln.enabled or not ln.text.strip() or ch is None:
            continue
        if ch.source != LineSource.TTS or ln.rec_path:
            continue
        h = tts_hash(ln.text, ch.tts_params())
        p = project.resolve(ln.tts_path)
        if force or ln.tts_hash != h or not p or not p.exists():
            out.append(ln)
    return out


def speech_duration(path: str) -> float:
    """Длительность звучащей части файла (без тишины по краям), сек."""
    import tempfile

    import soundfile as sf

    from . import ffmpeg
    from .mixer import silence_bounds
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "d.wav")
        ffmpeg.convert_audio(path, wav, 16000)
        x, sr = sf.read(wav, dtype="float32")
    a, b = silence_bounds(x, sr)
    return max(0.0, (b - a) / sr)


def line_slot(project: Project, line: Line) -> float:
    """Время, которое есть у реплики при сведении (как в mixer.available_time)."""
    from .mixer import available_time
    lines = sorted([x for x in project.lines if x.enabled], key=lambda x: x.start)
    for i, x in enumerate(lines):
        if x.id == line.id:
            return available_time(project, i, lines)
    return line.duration


def fit_rate(base_rate: int, duration: float, slot: float, max_boost: int) -> Optional[int]:
    """Темп синтеза (%), при котором реплика влезет в своё время, или None, если и так влезает.
    Темп у движков примерно пропорционален скорости речи; запас 4% на неточность."""
    if slot <= 0 or duration <= slot * 1.02 or max_boost <= 0:
        return None
    need = (1 + base_rate / 100.0) * (duration / slot) * 1.04 - 1
    new = min(base_rate + max_boost, 100, int(need * 100 + 0.999))
    return new if new > base_rate else None


def synthesize_lines(project: Project, lines: list[Line],
                     on_result: Callable[[SynthResult], None],
                     progress: Optional[Callable[[float, str], None]] = None,
                     cancel: Optional[Callable[[], bool]] = None) -> int:
    """Синтезировать реплики. Возвращает число ошибок.

    Если включена подгонка (mix.fit_by_rate), слишком длинная реплика синтезируется ещё раз
    с более быстрым темпом — это звучит естественнее, чем ускорение готового звука.
    Остаток (если не влезла и так) досжимается при сведении (mix.max_speedup)."""
    out_dir = project.project_dir / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    fit = project.mix.fit_by_rate
    boost = project.mix.max_rate_boost
    groups: dict[str, list[tuple[Line, str]]] = {}
    for ln in lines:
        ch = project.character(ln.character_id)
        if ch is None:
            continue
        h = tts_hash(ln.text, ch.tts_params())
        groups.setdefault(ch.engine.value, []).append((ln, h))

    total = sum(len(v) for v in groups.values())
    done = 0
    errors = 0

    def cached_file(stem: str) -> Optional[Path]:
        p = next((p for p in out_dir.glob(f"{stem}.*") if p.suffix in (".mp3", ".wav")), None)
        return p if p and p.stat().st_size > 0 else None

    for engine_id, items in groups.items():
        engine = get_engine(engine_id)
        ok, why = engine.available()
        if not ok:
            for ln, h in items:
                on_result(SynthResult(ln.id, error=why))
            errors += len(items)
            done += len(items)
            continue
        refit: list[tuple[Line, str, str, int]] = []   # (реплика, хэш, файл, новый темп)

        def finish(ln: Line, h: str, path: str):
            """Готовый звук реплики: при необходимости — в очередь на подгонку темпом."""
            nonlocal done
            if fit:
                ch = project.character(ln.character_id)
                try:
                    new_rate = fit_rate(ch.rate, speech_duration(path), line_slot(project, ln), boost)
                except Exception:  # noqa: BLE001  не удалось измерить — оставляем как есть
                    new_rate = None
                if new_rate is not None:
                    refit.append((ln, h, path, new_rate))
                    return
            done += 1
            on_result(SynthResult(ln.id, project.relative(Path(path)), h))
            if progress:
                progress(done / max(1, total), f"Синтез {done}/{total}")

        jobs = []
        pending: list[tuple[Line, str]] = []
        for ln, h in items:
            if cancel and cancel():
                break
            cached = cached_file(h)          # уже есть в кэше (тот же текст и голос)?
            if cached:
                finish(ln, h, str(cached))
                continue
            ch = project.character(ln.character_id)
            assert ch is not None
            jobs.append((ln.text.strip(), ch.voice, str(out_dir / h), ch.rate, ch.pitch))
            pending.append((ln, h))
        if progress:
            progress(done / max(1, total), f"Синтез: {engine.title}")

        def on_done(i: int, path: Optional[str], err: Optional[Exception]):
            nonlocal done, errors
            ln, h = pending[i]
            if err is not None or not path:
                done += 1
                errors += 1
                on_result(SynthResult(ln.id, error=str(err)))
                if progress:
                    progress(done / max(1, total), f"Синтез {done}/{total}")
            else:
                finish(ln, h, path)

        if jobs:
            engine.synthesize_many(jobs, on_done=on_done, cancel=cancel)

        # Подгонка длинных реплик темпом синтеза
        jobs2, pending2 = [], []
        for ln, h, path, new_rate in refit:
            stem = f"{h}_r{new_rate}"
            cached = cached_file(stem)
            if cached:
                done += 1
                on_result(SynthResult(ln.id, project.relative(cached), h))
                continue
            ch = project.character(ln.character_id)
            jobs2.append((ln.text.strip(), ch.voice, str(out_dir / stem), new_rate, ch.pitch))
            pending2.append((ln, h, path))
        if jobs2 and progress:
            progress(done / max(1, total), f"Подгонка темпа длинных реплик: {len(jobs2)}")

        def on_done2(i: int, path: Optional[str], err: Optional[Exception]):
            nonlocal done
            ln, h, first = pending2[i]
            done += 1
            # не вышло — оставляем первый вариант, его досожмёт сведение
            on_result(SynthResult(ln.id, project.relative(Path(path if path and not err else first)), h))
            if progress:
                progress(done / max(1, total), f"Синтез {done}/{total}")

        if jobs2:
            engine.synthesize_many(jobs2, on_done=on_done2, cancel=cancel)
    return errors


def preview_text(engine_id: str, voice: str, text: str, out_base: str,
                 rate: int = 0, pitch: int = 0) -> str:
    """Синтез для прослушивания голоса в настройках персонажа."""
    return get_engine(engine_id).synthesize(text, voice, out_base, rate, pitch)
