"""Разрезать реплику в позиции курсора и склеить несколько реплик в одну.

Запись при разрезании не теряется: обе части ссылаются на тот же файл дубля, просто у
первой обрезан конец, у второй — начало (стык сглажен короткими фейдами)."""

from __future__ import annotations

from typing import Optional

import soundfile as sf

from .models import Line, Project, new_id
from .rythmo import word_times, words_to_fractions

EDGE_FADE = 0.02
MIN_PART = 0.1


def _split_text(line: Line, t: float) -> tuple[str, str]:
    tokens = line.text.split()
    if len(tokens) < 2:
        return line.text, ""
    times = word_times(line)
    if [w for _a, _b, w in times] != tokens:
        # в тексте есть пометки в скобках — делим по доле слов
        k = round(len(tokens) * (t - line.start) / max(1e-6, line.duration))
    else:
        mids = [(a + b) / 2 for a, b, _w in times]
        k = sum(1 for m in mids if m < t)
    k = min(max(k, 1), len(tokens) - 1)
    return " ".join(tokens[:k]), " ".join(tokens[k:])


def split_line(project: Project, line: Line, t: float) -> Optional[Line]:
    """Разрезать реплику в момент t. Возвращает новую (вторую) реплику или None."""
    if not (line.start + MIN_PART <= t <= line.end - MIN_PART):
        return None
    left, right = _split_text(line, t)
    abs_words = [(line.start + a * line.duration, line.start + b * line.duration, w)
                 for a, b, w in line.words]
    new = Line(round(t, 3), line.end, right, line.character_id, id=new_id(),
               enabled=line.enabled, gain_db=line.gain_db)
    if line.rec_path:
        src = project.resolve(line.rec_path)
        try:
            dur = sf.info(str(src)).duration if src else 0.0
        except Exception:  # noqa: BLE001
            dur = 0.0
        file_t0 = line.start + line.rec_offset          # начало файла на шкале фильма
        cut = t - file_t0                              # где резать внутри файла
        if 0 < cut < dur:
            new.rec_path = line.rec_path
            new.rec_offset = round(file_t0 - t, 3)
            new.trim_in = round(max(line.trim_in, cut), 3)
            new.trim_out = line.trim_out
            new.fade_in = EDGE_FADE
            new.fade_out = line.fade_out
            new.takes = {k: round(v + line.start - t, 3) for k, v in line.takes.items()}
            line.trim_out = round(max(line.trim_out, dur - cut), 3)
            line.fade_out = EDGE_FADE
        elif cut <= 0:                                  # вся запись во второй части
            new.rec_path, new.rec_offset = line.rec_path, round(file_t0 - t, 3)
            new.trim_in, new.trim_out = line.trim_in, line.trim_out
            new.fade_in, new.fade_out = line.fade_in, line.fade_out
            new.takes = {k: round(v + line.start - t, 3) for k, v in line.takes.items()}
            line.rec_path, line.rec_offset, line.takes = "", 0.0, {}
            line.trim_in = line.trim_out = line.fade_in = line.fade_out = 0.0
    line.end = round(t, 3)
    line.text = left
    if abs_words:
        line.words = words_to_fractions(line.start, line.end, [w for w in abs_words if w[0] < t])
        new.words = words_to_fractions(new.start, new.end, [w for w in abs_words if w[0] >= t])
    i = project.lines.index(line)
    project.lines.insert(i + 1, new)
    return new


def merge_lines(project: Project, lines: list[Line]) -> tuple[Optional[Line], str]:
    """Склеить реплики одного персонажа. Возвращает итоговую реплику и предупреждение."""
    lines = sorted(lines, key=lambda x: x.start)
    if len(lines) < 2:
        return None, "Выделите две реплики или больше."
    if len({ln.character_id for ln in lines}) > 1:
        return None, "Склеивать можно только реплики одного персонажа."
    first = lines[0]
    start, end = min(ln.start for ln in lines), max(ln.end for ln in lines)
    with_rec = [ln for ln in lines if ln.rec_path]
    warn = ""
    keep = with_rec[0] if with_rec else None
    # части одного дубля (после разрезания): тот же файл с тем же началом — склеиваем звук
    same = len(with_rec) > 1 and len({ln.rec_path for ln in with_rec}) == 1 and \
        len({round(ln.start + ln.rec_offset, 3) for ln in with_rec}) == 1
    if same:
        last = with_rec[-1]
        keep.trim_out, keep.fade_out = last.trim_out, last.fade_out
    elif len(with_rec) > 1:
        warn = (f"У {len(with_rec)} реплик были записи — оставлена запись первой из них, "
                "остальные части нужно перезаписать.")
    abs_words = []
    for ln in lines:
        abs_words += [(ln.start + a * ln.duration, ln.start + b * ln.duration, w)
                      for a, b, w in ln.words]
    if keep is not None and keep is not first:
        first.rec_path, first.takes = keep.rec_path, dict(keep.takes)
        first.trim_in, first.trim_out = keep.trim_in, keep.trim_out
        first.fade_in, first.fade_out = keep.fade_in, keep.fade_out
    if keep is not None:
        first.rec_offset = round(keep.start + keep.rec_offset - start, 3)
        first.takes = {k: round(v + keep.start - start, 3) for k, v in keep.takes.items()}
        if first.rec_path:
            first.takes[first.rec_path] = first.rec_offset
    first.text = " ".join(ln.text for ln in lines if ln.text.strip())
    first.orig_text = " ".join(ln.orig_text for ln in lines if ln.orig_text.strip())
    first.note = "; ".join(ln.note for ln in lines if ln.note.strip())
    first.start, first.end = start, end
    full = all(ln.words for ln in lines)
    first.words = words_to_fractions(start, end, abs_words) if full else []
    for ln in lines[1:]:
        project.lines.remove(ln)
    return first, warn
