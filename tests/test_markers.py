import os

from voiceover.core.models import Project, add_marker


def test_model(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"))
    add_marker(p, 5.0, " музыка громче ")
    add_marker(p, 2.0, "крик")
    assert [m["t"] for m in p.markers] == [2.0, 5.0] and p.markers[1]["text"] == "музыка громче"
    p.save()
    assert Project.load(p.path).markers == p.markers
    st = p.snapshot()
    p.markers.clear()
    p.restore(st)
    assert len(p.markers) == 2
    assert Project.from_dict({"markers": [1, {"id": "a", "t": 1, "text": "x"}]}).markers == \
        [{"id": "a", "t": 1, "text": "x"}]


def test_window(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui import dialogs, timeline
    from voiceover.ui.main_window import MainWindow
    (tmp_path / "v.mp4").write_bytes(b"")
    p = Project(video_path=str(tmp_path / "v.mp4"), duration=60)
    w = MainWindow(AppSettings())
    w.resize(1200, 800)
    w.set_project(p)
    w.timeline.set_duration(60)
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "здесь шум")
    c = w.timeline.canvas
    c.resize(1000, 300)
    x = c.x_of(4.0)
    c.mouseDoubleClickEvent(QMouseEvent(QMouseEvent.MouseButtonDblClick, QPointF(x, 12),
                                        QPointF(x, 12), Qt.LeftButton, Qt.LeftButton,
                                        Qt.NoModifier))
    assert len(p.markers) == 1 and abs(p.markers[0]["t"] - 4.0) < 0.05 and w.dirty
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)   # окно правки метки
    got = []
    c.markerClicked.connect(got.append)
    c.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(x + 3, 6), QPointF(x + 3, 6),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert got == [p.markers[0]["id"]]
    seeks = []
    monkeypatch.setattr(w.player, "seek", seeks.append)
    monkeypatch.setattr(w.player, "position", lambda: 0.0)
    w.goto_marker(1)
    assert seeks and abs(seeks[0] - p.markers[0]["t"]) < 1e-9
    assert not c.grab().isNull()
    w.dirty = False
    w.close()
    assert timeline
    del app
