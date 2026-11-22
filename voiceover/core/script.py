"""Сценарий для актёров: сводка по ролям и реплики каждой роли с таймкодами и заметками.
Собирается как HTML; интерфейс сохраняет его в PDF или HTML."""

from __future__ import annotations

import datetime
import html
import re
from pathlib import Path
from typing import Iterable

from ..i18n import plural, tr
from .models import DONE_STATUSES, Project, line_status
from .rythmo import spoken_text
from .subtitles import fmt_clock

_NOTE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def word_count(text: str) -> int:
    return len(spoken_text(text).split())


def stats(project: Project) -> list[dict]:
    out = []
    for c in project.characters:
        lines = [ln for ln in project.lines if ln.character_id == c.id and ln.enabled]
        out.append({"id": c.id, "name": c.name, "actor": c.actor, "lines": len(lines),
                    "words": sum(word_count(ln.text) for ln in lines),
                    "recorded": sum(1 for ln in lines if line_status(project, ln) in DONE_STATUSES)})
    return out


def _text_html(text: str) -> str:
    out, pos = [], 0
    for m in _NOTE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<i style="color:#777">{html.escape(m.group())}</i>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def build_html(project: Project, character_ids: Iterable[str]) -> str:
    cids = list(character_ids)
    title = Path(project.path).stem if project.path else tr("Проект")
    numbers = {ln.id: i + 1 for i, ln in enumerate(project.lines)}
    st = {s["id"]: s for s in stats(project)}
    css = ("body{font-family:sans-serif;font-size:10pt;color:#111}"
           "h1{font-size:18pt;margin:0 0 4px}h2{font-size:14pt;margin:14px 0 2px}"
           "table{border-collapse:collapse;width:100%}"
           "th{text-align:left;border-bottom:1px solid #444;padding:3px 6px;font-size:9pt}"
           "td{border-bottom:1px solid #ccc;padding:4px 6px;vertical-align:top}"
           ".m{color:#666;font-size:9pt}.c{text-align:center}")
    out = [f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
           f"<style>{css}</style></head><body>",
           f"<h1>{html.escape(title)} — {tr('сценарий озвучки')}</h1>",
           f"<p class='m'>{datetime.date.today():%d.%m.%Y}. "
           f"{tr('Пометки в скобках не произносятся. Знак ́ — ударение.')}</p>",
           "<table width='100%' cellspacing='0'><tr>"
           + "".join(f"<th>{tr(h)}</th>" for h in ("Персонаж", "Актёр", "Реплик", "Слов", "Записано"))
           + "</tr>"]
    for cid in cids:
        s = st.get(cid)
        if s:
            out.append(f"<tr><td>{html.escape(s['name'])}</td><td>{html.escape(s['actor'])}</td>"
                       f"<td>{s['lines']}</td><td>{s['words']}</td>"
                       f"<td>{s['recorded']} {tr('из')} {s['lines']}</td></tr>")
    out.append("</table>")
    for cid in cids:
        c = project.character(cid)
        if c is None:
            continue
        lines = [ln for ln in project.lines if ln.character_id == cid and ln.enabled]
        out.append("<div style='page-break-before:always'></div>")
        actor = f" — {html.escape(c.actor)}" if c.actor else ""
        out.append(f"<h2>{html.escape(c.name)}{actor}</h2>"
                   f"<p class='m'>{len(lines)} {plural(len(lines), 'реплика', 'реплики', 'реплик')}, "
                   f"{st[cid]['words']} {plural(st[cid]['words'], 'слово', 'слова', 'слов')}</p>")
        out.append(f"<table width='100%' cellspacing='0'><tr><th width='5%'>№</th>"
                   f"<th width='13%'>{tr('Время')}</th><th width='55%'>{tr('Текст')}</th>"
                   f"<th>{tr('Заметка')}</th>"
                   "<th width='4%'>✓</th></tr>")
        for ln in lines:
            status = line_status(project, ln)
            done = "✓" if status in DONE_STATUSES else ("↺" if status == "redo" else "")
            if status == "redo":
                ln_note = tr("Переписать") + (f": {ln.review_note}" if ln.review_note else "")
                ln = type(ln)(**{**ln.to_dict(), "note": "; ".join(x for x in (ln_note, ln.note) if x)})
            orig = (f"<br><span class='m'>{html.escape(ln.orig_text)}</span>"
                    if ln.orig_text else "")
            out.append(f"<tr><td class='m'>{numbers[ln.id]}</td>"
                       f"<td class='m'>{fmt_clock(ln.start)}</td>"
                       f"<td>{_text_html(ln.text)}{orig}</td>"
                       f"<td class='m'>{html.escape(ln.note)}</td><td class='c'>{done}</td></tr>")
        out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out)
