import os

import numpy as np
import soundfile as sf

from voiceover.core import mixer
from voiceover.core.models import Line, Project

SR = 48000


def make(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=4)
    p.project_dir.mkdir()
    sf.write(p.project_dir / "o.wav", np.zeros((4 * SR, 2), np.float32), SR)
    p.original_wav = "o.wav"
    a, b = p.add_character("A"), p.add_character("B")
    for name in ("a", "b", "n"):
        sf.write(p.project_dir / f"{name}.wav", np.full(SR // 2, 0.1, np.float32), SR)
    p.lines += [Line(0.5, 1.0, "a", a.id, rec_path="a.wav"), Line(1.5, 2.0, "b", b.id, rec_path="b.wav"),
                Line(2.5, 3.0, "n", None, rec_path="n.wav")]
    return p, a, b


def ids(p):
    by = {ln.id: ln.text for ln in p.lines}
    return sorted(by[c.line_id] for c in mixer.prepare_clips(p)[0])


def test_mute_and_solo(tmp_path):
    p, a, b = make(tmp_path)
    assert ids(p) == ["a", "b", "n"]
    p.mix.muted_roles = [a.id, ""]
    assert ids(p) == ["b"]
    p.mix.solo_roles = [a.id]                 # соло важнее заглушения
    assert ids(p) == ["a"]
    p.mix.solo_roles, p.mix.muted_roles = [b.id, ""], []
    assert ids(p) == ["b", "n"]
    _clips, warns = mixer.prepare_clips(p)
    assert not warns                         # заглушённые — не «нет записи»


def test_timeline_buttons(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.ui.timeline import Timeline
    p, a, b = make(tmp_path)
    t = Timeline()
    t.resize(800, 300)
    t.set_project(p)
    c = t.canvas
    got = []
    c.roleToggled.connect(lambda k, r: got.append((k, r)))
    for lane, kind in ((0, "solo"), (1, "mute"), (2, "mute")):
        pt = c.role_btn_rect(lane, kind).center()
        c.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pt, pt, Qt.LeftButton,
                                      Qt.LeftButton, Qt.NoModifier))
    assert got == [("solo", a.id), ("mute", b.id), ("mute", "")]
    p.mix.muted_roles = [b.id]
    assert not c.grab().isNull()
    _keep = t
    del app
