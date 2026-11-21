import os

from voiceover.core.models import Line, Project
from voiceover.core.script import build_html, stats, word_count


def make():
    p = Project(path="/tmp/ep01.vop")
    a, b = p.add_character("Наруто", actor="Иван"), p.add_character("Сакура")
    p.lines += [Line(1, 2, "(смеётся) Я стану Хокаге!", a.id, note="громко"),
                Line(3, 4, "<b>Наруто</b>!", b.id), Line(5, 6, "Есть!", a.id)]
    return p, a, b


def test_stats_and_html():
    p, a, b = make()
    assert word_count("(смеётся) Я стану Хокаге!") == 3
    s = {x["name"]: x for x in stats(p)}
    assert s["Наруто"]["lines"] == 2 and s["Наруто"]["words"] == 4 and s["Наруто"]["recorded"] == 0
    h = build_html(p, [a.id])
    assert "Иван" in h and "Хокаге" in h and "<i" in h and "громко" in h
    assert "Сакура" not in h
    h2 = build_html(p, [b.id])
    assert "&lt;b&gt;Наруто&lt;/b&gt;" in h2       # текст экранирован


def test_pdf(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    from voiceover.ui import team_tools
    p, a, b = make()

    class W:
        project = p
        msgs = []

        def statusBar(self):
            return self

        def showMessage(self, m, *_):
            self.msgs.append(m)

    w = W()
    monkeypatch.setattr(team_tools, "choose_characters", lambda *x, **k: [a.id, b.id])
    out = tmp_path / "s.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *x, **k: (str(out), "PDF (*.pdf)"))
    team_tools.export_script(w)
    assert out.read_bytes()[:4] == b"%PDF" and out.stat().st_size > 2000
    del app
