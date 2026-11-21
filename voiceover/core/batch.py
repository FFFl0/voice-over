"""Пакетная обработка серий: по очереди для каждого проекта — извлечь звук, отделить
голоса, экспортировать видео и дорожки. Ошибка в одной серии не останавливает остальные."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import ffmpeg
from .ffmpeg import Cancelled
from .models import MixMode, Project

STEPS = {
    "extract": "Извлечь звук оригинала (если его нет)",
    "separate": "Отделить голоса (если ещё не отделены)",
    "export": "Экспорт видео с озвучкой",
    "stems": "Экспорт по дорожкам (для Reaper)",
}


@dataclass
class BatchItem:
    path: str
    ok: bool = True
    done: list = field(default_factory=list)
    message: str = ""
    warnings: list = field(default_factory=list)


def export_path(project: Project, container: str) -> str:
    v = Path(project.video_path)
    return str(v.with_name(v.stem + ".voiceover." + container))


def run_batch(paths: list[str], steps: set[str], opts: ffmpeg.ExportOptions,
              demucs_model: str = "htdemucs", demucs_device: str = "auto",
              burn_subs: bool = False, overwrite: bool = False,
              progress: Optional[Callable[[float, str], None]] = None,
              cancel: Optional[Callable[[], bool]] = None) -> list[BatchItem]:
    from .export import export_project, write_burn_subs
    from .separation import separate_vocals
    from .stems import export_stems
    out: list[BatchItem] = []
    n = len(paths)
    for i, path in enumerate(paths):
        item = BatchItem(path)
        out.append(item)
        name = Path(path).stem

        def rep(f, msg, i=i, name=name):
            if progress:
                progress((i + max(0.0, min(1.0, f))) / n, f"[{i + 1}/{n}] {name}: {msg}")

        try:
            if cancel and cancel():
                raise Cancelled()
            p = Project.load(path)
            if not p.video_path or not Path(p.video_path).exists():
                raise RuntimeError(f"не найдено видео: {p.video_path}")
            orig = p.resolve(p.original_wav)
            if "extract" in steps and (not orig or not orig.exists()):
                o = p.project_dir / "original.wav"
                ffmpeg.extract_audio(p.video_path, str(o), p.audio_stream, p.mix.sample_rate,
                                     p.duration, lambda f: rep(f * 0.1, "извлечение звука"), cancel)
                p.original_wav = p.relative(o)
                p.save()
                item.done.append("звук извлечён")
            orig = p.resolve(p.original_wav)
            bg = p.resolve(p.background_wav)
            if "separate" in steps and (overwrite or not bg or not bg.exists()):
                if not orig or not orig.exists():
                    raise RuntimeError("нет звука оригинала — отметьте «Извлечь звук»")
                bgp = p.project_dir / "background.wav"
                separate_vocals(str(orig), str(bgp), str(p.project_dir / "vocals.wav"),
                                demucs_model, demucs_device,
                                lambda f, m: rep(0.1 + f * 0.5, "отделение голосов"), cancel)
                p.background_wav = p.relative(bgp)
                p.save()
                item.done.append("голоса отделены")
            if "export" in steps:
                if p.mix.mode == MixMode.DUBBING and not (p.resolve(p.background_wav) or
                                                          Path("")).exists():
                    raise RuntimeError("режим дубляжа: голоса ещё не отделены")
                target = export_path(p, opts.container)
                if Path(target).exists() and not overwrite:
                    item.done.append("видео уже есть — пропущено")
                else:
                    o = copy.copy(opts)
                    if burn_subs:
                        o.burn_subs = write_burn_subs(p, str(p.project_dir / "burn.ass"))
                    item.warnings += export_project(p, target, o, "video",
                                                    lambda f, m: rep(0.6 + f * 0.3, m), cancel)
                    item.done.append(f"видео: {Path(target).name}")
            if "stems" in steps:
                d = Path(p.video_path).with_name(Path(p.video_path).stem + " — дорожки")
                export_stems(p, str(d), lambda f, m: rep(0.9 + f * 0.1, m), cancel)
                item.done.append(f"дорожки: {d.name}")
        except Cancelled:
            item.ok, item.message = False, "отменено"
            raise
        except Exception as e:  # noqa: BLE001  ошибка одной серии не останавливает остальные
            item.ok, item.message = False, str(e) or e.__class__.__name__
    if progress:
        progress(1.0, "Пакетная обработка завершена")
    return out
