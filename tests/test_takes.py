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


def test_loop_recording_flow(tmp_path, monkeypatch):
    """По кругу: после автостопа запись той же реплики начинается снова; «Стоп» в паузе
    между дублями заканчивает круг."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui import record_panel as rp
    from voiceover.ui.video_player import VideoPlayer

    class FakeRec:
        recording = False
        level = 0.0

        def start(self, path):
            self.recording = True
            sf.write(path, np.full(4800, 0.3, np.float32), 48000)

        def stop(self):
            self.recording = False

        def close(self):
            pass

    p, ln = make(tmp_path, n=1)
    panel = rp.RecordPanel(VideoPlayer(), AppSettings())
    panel.setEnabled(True)
    fake = FakeRec()
    panel.rec = fake
    monkeypatch.setattr(panel, "_recorder", lambda: fake)
    monkeypatch.setattr(panel, "isVisible", lambda: True)
    panel.auto_align.setChecked(False)
    panel.loop.setChecked(True)
    panel.set_project(p)
    panel.set_line(ln)
    panel.start_recording()
    assert panel.is_recording()
    panel.stop_recording()                     # автостоп
    assert panel.loop_pending and not panel.is_recording()
    panel._loop_again()                        # таймер паузы
    assert panel.is_recording() and len(list_takes(p, ln)) == 3
    panel.stop_recording()
    panel.stop_recording(manual=True)          # «Стоп» в паузе
    assert not panel.loop_pending
    panel._loop_again()
    assert not panel.is_recording()
    del app


def test_take_editor_drag(tmp_path):
    """Обрезка, фейд и сдвиг мышью в визуальном редакторе дубля."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.record_panel import RecordPanel
    from voiceover.ui.video_player import VideoPlayer
    p, ln = make(tmp_path, n=1)                          # дубль 0.1 с — мало, перепишем на 2 с
    sf.write(p.resolve(ln.rec_path), (0.3 * np.sin(np.arange(96000) / 7)).astype(np.float32), 48000)
    ln.rec_offset = 0.0
    panel = RecordPanel(VideoPlayer(), AppSettings())
    panel.setEnabled(True)
    panel.set_project(p)
    panel.set_line(ln)
    ed = panel.editor
    ed.resize(800, 190)
    assert abs(ed.dur - 2.0) < 1e-6
    got = []
    panel.recorded.connect(got.append)

    def drag(x0, y0, x1):
        ed.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(x0, y0),
                                       QPointF(x0, y0), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        ed.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, QPointF(x1, y0), QPointF(x1, y0),
                                      Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        ed.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(x1, y0),
                                         QPointF(x1, y0), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    mid_y = ed.height() / 2
    px_per_s = ed._x(1.0) - ed._x(0.0)
    xa = ed._x(ln.start + ln.rec_offset)
    drag(xa, mid_y, xa + 0.3 * px_per_s)                 # начало — обрезать 0.3 с
    assert abs(ln.trim_in - 0.3) < 0.02 and abs(panel.trim_in.value() - ln.trim_in) < 1e-9
    xb = ed._x(ln.start + ln.rec_offset + ed.dur)
    drag(xb, mid_y, xb - 0.5 * px_per_s)                 # конец — 0.5 с
    assert abs(ln.trim_out - 0.5) < 0.02
    ka, _kb = ed._kept()
    drag(ed._x(ka), 18, ed._x(ka) + 0.2 * px_per_s)      # квадратик нарастания
    assert abs(ln.fade_in - 0.2) < 0.02 and ln.trim_in > 0.25   # обрезка не задета
    drag(ed._x(ka + 0.6), mid_y, ed._x(ka + 0.6) + 0.25 * px_per_s)   # сдвиг всей записи
    assert abs(ln.rec_offset - 0.25) < 0.02 and abs(panel.offset.value() - ln.rec_offset) < 1e-9
    assert len(got) == 4
    ed.mouseDoubleClickEvent(QMouseEvent(QMouseEvent.MouseButtonDblClick,
                                         QPointF(ed._x(ed._kept()[0]), mid_y),
                                         QPointF(0, 0), Qt.LeftButton, Qt.LeftButton,
                                         Qt.NoModifier))
    assert ln.trim_in == 0.0
    assert not ed.grab().isNull()
    del app
