import os

import numpy as np
import soundfile as sf

from voiceover.core.models import Line, Project, line_status
from voiceover.core.script import build_html, stats
from voiceover.core.takes import select_take


def make(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=10)
    p.project_dir.mkdir()
    ch = p.add_character("A")
    sf.write(p.project_dir / "r.wav", np.zeros(4800, np.float32), 48000)
    p.lines += [Line(1, 2, "раз", ch.id, rec_path="r.wav"), Line(3, 4, "два", ch.id)]
    return p


def test_statuses(tmp_path):
    p = make(tmp_path)
    a, b = p.lines
    assert line_status(p, a) == "rec" and line_status(p, b) == "none"
    a.review = "ok"
    assert line_status(p, a) == "ok"
    a.review, a.review_note = "redo", "громче"
    b.review = "redo"
    assert line_status(p, a) == "redo" and line_status(p, b) == "redo"
    assert stats(p)[0]["recorded"] == 0
    h = build_html(p, [p.characters[0].id])
    assert "Переписать: громче" in h and "↺" in h
    a.review = "ok"
    select_take(a, "other.wav")              # другой дубль — одобрение снимается
    assert a.review == ""
    q = Project.from_dict(p.to_dict())
    assert q.lines[0].review_note == "громче"


def test_window_review(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui import dialogs
    from voiceover.ui.main_window import MainWindow
    p = make(tmp_path)
    (tmp_path / "v.mp4").write_bytes(b"")
    p.video_path = str(tmp_path / "v.mp4")
    w = MainWindow(AppSettings())
    w.set_project(p)
    w.table.selectAll()
    w.set_review("ok")                        # одобряется только записанная
    assert [ln.review for ln in p.lines] == ["ok", ""]
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "  интонация  ")
    w.set_review("redo")
    assert [(ln.review, ln.review_note) for ln in p.lines] == [("redo", "интонация")] * 2
    assert w.stat_labels["redo"].text() == "2" and w.stat_labels["ready"].text() == "0 из 2"
    w.record.set_line(p.lines[0])
    assert w.record.review_lbl.isVisibleTo(w.record) and "интонация" in w.record.review_lbl.text()
    w.lines_filter.set_filter("", "*", "redo")
    assert w.lines_filter.rowCount() == 2
    w.set_review("")
    assert all(not ln.review and not ln.review_note for ln in p.lines)
    assert not w.timeline.canvas.grab().isNull()
    w.dirty = False
    w.close()
    del app
