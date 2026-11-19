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


def synthesize_lines(project: Project, lines: list[Line],
                     on_result: Callable[[SynthResult], None],
                     progress: Optional[Callable[[float, str], None]] = None,
                     cancel: Optional[Callable[[], bool]] = None) -> int:
    """Синтезировать реплики. Возвращает число ошибок."""
    out_dir = project.project_dir / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
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
    for engine_id, items in groups.items():
        engine = get_engine(engine_id)
        ok, why = engine.available()
        if not ok:
            for ln, h in items:
                on_result(SynthResult(ln.id, error=why))
            errors += len(items)
            done += len(items)
            continue
        jobs = []
        pending: list[tuple[Line, str]] = []
        for ln, h in items:
            # уже есть в кэше (тот же текст и голос)?
            cached = next((p for p in out_dir.glob(f"{h}.*") if p.suffix in (".mp3", ".wav")), None)
            if cached and cached.stat().st_size > 0:
                on_result(SynthResult(ln.id, project.relative(cached), h))
                done += 1
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
            done += 1
            if err is not None or not path:
                errors += 1
                on_result(SynthResult(ln.id, error=str(err)))
            else:
                on_result(SynthResult(ln.id, project.relative(Path(path)), h))
            if progress:
                progress(done / max(1, total), f"Синтез {done}/{total}")

        if jobs:
            engine.synthesize_many(jobs, on_done=on_done, cancel=cancel)
    return errors


def preview_text(engine_id: str, voice: str, text: str, out_base: str,
                 rate: int = 0, pitch: int = 0) -> str:
    """Синтез для прослушивания голоса в настройках персонажа."""
    return get_engine(engine_id).synthesize(text, voice, out_base, rate, pitch)
