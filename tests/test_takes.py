import os

import numpy as np
import soundfile as sf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from voiceover.core.models import Line, Project  # noqa: E402
from voiceover.core.takes import delete_take, list_takes, select_take  # noqa: E402


def make(tmp_path, n=3):
    p = Project(path=str(tmp_path / "p.vop"), duration=10)
    ln = Line(1.0, 2.0, "раз")
    p.lines.append(ln)
    rec = p.project_dir / "rec"
    rec.mkdir(parents=True)
    for i in range(1, n + 1):
        sf.write(rec / f"{ln.id}_{i}.wav", np.zeros(4800 * i, np.float32), 48000)
        ln.takes[f"rec/{ln.id}_{i}.wav"] = 0.1 * i
    ln.rec_path, ln.rec_offset = f"rec/{ln.id}_{n}.wav", 0.1 * n
    return p, ln


def test_list_and_select(tmp_path):
    p, ln = make(tmp_path)
    tk = list_takes(p, ln)
    assert [t.number for t in tk] == [1, 2, 3] and abs(tk[1].duration - 0.2) < 1e-6
    ln.rec_offset = 0.55                       # подвинули текущий дубль
    select_take(ln, tk[0].rel)
    assert ln.rec_path == tk[0].rel and abs(ln.rec_offset - 0.1) < 1e-9
    select_take(ln, tk[2].rel)                 # вернулись — сдвиг сохранился
    assert abs(ln.rec_offset - 0.55) < 1e-9
    select_take(ln, "")
    assert ln.rec_path == "" and ln.rec_offset == 0.0


def test_delete_current_picks_last(tmp_path):
    p, ln = make(tmp_path)
    cur = ln.rec_path
    delete_take(p, ln, cur)
    assert not p.resolve(cur).exists()
    assert ln.rec_path.endswith("_2.wav") and abs(ln.rec_offset - 0.2) < 1e-9
    assert len(list_takes(p, ln)) == 2


def test_takes_saved_in_project(tmp_path):
    p, ln = make(tmp_path)
    p.save()
    q = Project.load(p.path)
    assert q.lines[0].takes == ln.takes


def test_record_panel_take_box(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.record_panel import RecordPanel
    from voiceover.ui.video_player import VideoPlayer
    p, ln = make(tmp_path)
    player = VideoPlayer()
    panel = RecordPanel(player, AppSettings())
    panel.setEnabled(True)
    panel.set_project(p)
    panel.set_line(ln)
    box = panel.take_box
    assert box.count() == 4 and box.currentData() == ln.rec_path
    got = []
    panel.recorded.connect(got.append)
    panel._take_chosen(1)
    assert ln.rec_path.endswith("_1.wav") and got == [ln.id]
    assert abs(panel.offset.value() - 0.1) < 1e-9
    del app
