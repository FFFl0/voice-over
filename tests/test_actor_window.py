import os


def test_actor_window(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtMultimedia import QVideoFrame
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.models import Line, Project
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    (tmp_path / "v.mp4").write_bytes(b"")
    p = Project(video_path=str(tmp_path / "v.mp4"), duration=10)
    ch = p.add_character("Наруто")
    p.lines += [Line(1, 2, "(смеётся) Я стану <Хокаге>!", ch.id), Line(3, 4, "Есть!", ch.id)]
    w = MainWindow(AppSettings())
    w.set_project(p)
    w.toggle_actor_window(True)
    aw = w.actor_win
    assert aw is not None and aw.isVisible() and w.a_actor.isChecked()
    w.record.set_line(p.lines[0], "", "Есть!")
    assert "&lt;Хокаге&gt;" in aw.prompt.text() and aw.who.text() == "Наруто"
    assert aw.next_lbl.text() == "Дальше: Есть!"
    img = QImage(64, 36, QImage.Format_RGB32)
    img.fill(QColor("red"))
    w.player.video.videoSink().setVideoFrame(QVideoFrame(img))
    app.processEvents()
    assert aw.view.image is not None and aw.view.image.width() == 64
    w.record.cueState.emit("prep", 2.0, 0.1)
    assert aw.view.mode == "prep"
    assert not aw.grab().isNull()
    w.toggle_actor_window(False)
    assert w.actor_win is None and not w.a_actor.isChecked()
    w.record.set_line(p.lines[1])              # после закрытия связи разорваны — без ошибок
    w.dirty = False
    w.close()
    del app
