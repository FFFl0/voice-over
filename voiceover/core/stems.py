"""Экспорт по дорожкам для доведения звука в DAW: голоса каждого персонажа, фон и общий
микс отдельными WAV во всю длину фильма, плюс проект Reaper с репликами отдельными
клипами (их можно двигать, резать и обрабатывать)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from . import mixer
from .models import Project

ProgressCb = Callable[[float, str], None]


def safe_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s[:60] or "без имени"


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def export_stems(project: Project, out_dir: str, progress: Optional[ProgressCb] = None,
                 cancel: Optional[Callable[[], bool]] = None, reaper: bool = True) -> list[str]:
    """Возвращает список созданных файлов."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sr = project.mix.sample_rate

    def rep(f, msg):
        if progress:
            progress(f, msg)

    clips, _warn = mixer.prepare_clips(project, lambda f, m: rep(f * 0.2, m), cancel)
    by_line = {ln.id: ln for ln in project.lines}
    groups: dict[Optional[str], list] = {}
    for c in clips:
        ln = by_line.get(c.line_id)
        cid = ln.character_id if ln and project.character(ln.character_id) else None
        groups.setdefault(cid, []).append(c)
    files: list[str] = []
    jobs = [("Фон", "bg", clips)]
    for cid, cl in groups.items():
        ch = project.character(cid)
        jobs.append((f"Голоса — {ch.name if ch else 'без роли'}", "voice", cl))
    jobs.append(("Микс", "mix", clips))
    names: dict[str, str] = {}
    for k, (title, kind, cl) in enumerate(jobs):
        base, n = safe_name(title), 2
        name = base
        while name in names.values():
            name, n = f"{base} {n}", n + 1
        path = out / f"{name}.wav"
        a, b = 0.2 + 0.7 * k / len(jobs), 0.2 + 0.7 * (k + 1) / len(jobs)
        mixer.render_mix(project, cl, str(path), lambda f, m: rep(a + (b - a) * f, title), cancel,
                         include_background=kind != "voice", include_voices=kind != "bg")
        names[kind + str(k)] = name
        files.append(str(path))
    if reaper:
        files.append(write_reaper(project, out, clips, groups, sr))
    rep(1.0, "Дорожки готовы")
    return files


def write_reaper(project: Project, out: Path, clips, groups, sr: int) -> str:
    """Проект Reaper: дорожка «Фон» (файл Фон.wav) и по дорожке на персонажа, где каждая
    реплика — отдельный клип из подготовленной записи (с обработкой, без громкости)."""
    clip_dir = out / "Реплики"
    clip_dir.mkdir(exist_ok=True)
    by_line = {ln.id: ln for ln in project.lines}
    total = max([project.duration] + [c.end(sr) for c in clips])
    lines = ['<REAPER_PROJECT 0.1 "6.0" 0',
             f"  SAMPLERATE {sr} 0 0",
             "  <TRACK",
             f"    NAME {_q('Фон')}",
             "    <ITEM",
             "      POSITION 0",
             f"      LENGTH {total:.6f}",
             f"      NAME {_q('Фон')}",
             "      <SOURCE WAVE",
             f"        FILE {_q('Фон.wav')}",
             "      >",
             "    >",
             "  >"]
    num = 0
    for cid, cl in groups.items():
        ch = project.character(cid)
        cname = ch.name if ch else "без роли"
        color = ""
        if ch and re.fullmatch(r"#[0-9a-fA-F]{6}", ch.color):
            r, g, b = (int(ch.color[i:i + 2], 16) for i in (1, 3, 5))
            color = f"    PEAKCOL {(b << 16 | g << 8 | r) | 0x1000000}"
        lines += ["  <TRACK", f"    NAME {_q(cname)}"] + ([color] if color else [])
        for c in sorted(cl, key=lambda c: c.start):
            num += 1
            ln = by_line.get(c.line_id)
            fname = f"{num:04d} {safe_name(cname)}.wav"
            shutil.copyfile(c.path, clip_dir / fname)
            lines += ["    <ITEM",
                      f"      POSITION {c.start:.6f}",
                      f"      LENGTH {c.length / sr:.6f}",
                      f"      SOFFS {c.skip / sr:.6f}",
                      f"      FADEIN 1 {max(c.fade_in / sr, 0.005):.4f} 0",
                      f"      FADEOUT 1 {max(c.fade_out / sr, 0.005):.4f} 0",
                      f"      VOLPAN {c.gain:.6f} 0 1 -1",
                      f"      NAME {_q((ln.text if ln else '')[:80])}",
                      "      <SOURCE WAVE",
                      f"        FILE {_q('Реплики/' + fname)}",
                      "      >",
                      "    >"]
        lines.append("  >")
    lines.append(">")
    path = out / "Озвучка.RPP"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
