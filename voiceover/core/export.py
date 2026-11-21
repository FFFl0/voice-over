"""Полный экспорт проекта: сведение, громкость по стандарту, сборка видео или звука.
Используется и окном экспорта, и пакетной обработкой серий."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import ffmpeg, mixer
from .models import Project
from .subtitles import build_subtitle_items, write_ass

ProgressCb = Callable[[float, str], None]


def write_burn_subs(project: Project, path: str, with_speaker: bool = False) -> str:
    info = ffmpeg.probe(project.video_path)
    w, h = info.video_size
    items = build_subtitle_items(project.lines, project.character, "text", with_speaker)
    write_ass(path, items, Path(project.video_path).stem, False, (w or 1920, h or 1080))
    return path


def export_project(project: Project, out: str, opts: ffmpeg.ExportOptions, kind: str = "video",
                   progress: Optional[ProgressCb] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> list[str]:
    """kind: video | audio. Возвращает предупреждения сведения."""
    p = project

    def rep(f, s):
        if progress:
            progress(f, s)

    mix_wav = str(p.project_dir / "mix.wav")
    warnings = mixer.render_project(p, mix_wav, lambda f, s: rep(f * 0.45, s), cancel)
    final_wav = mix_wav
    if opts.loudness is not None:
        final_wav = str(p.project_dir / "mix_loudnorm.wav")
        m = ffmpeg.normalize_loudness(
            mix_wav, final_wav, opts.loudness, sample_rate=p.mix.sample_rate, duration=p.duration,
            progress=lambda f: rep(0.45 + f * 0.2, f"Громкость до {opts.loudness:g} LUFS (EBU R128)"),
            cancel=cancel)
        try:
            warnings.append(f"Громкость: была {float(m['input_i']):.1f} LUFS, "
                            f"приведена к {opts.loudness:g} LUFS")
        except (KeyError, ValueError):
            pass
    title = "Сборка файла (FFmpeg)" + (" — перекодирование видео" if opts.reencode else "")
    prog = lambda f: rep(0.65 + f * 0.35, title)  # noqa: E731
    if kind == "video":
        ffmpeg.mux_video(p.video_path, final_wav, out, opts, p.audio_stream, p.duration,
                         prog, cancel)
    else:
        ffmpeg.encode_audio(final_wav, out, opts, p.duration, prog, cancel)
    return warnings
