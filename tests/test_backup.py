import os
import time

from voiceover.core import backup
from voiceover.core.models import Line, Project


def test_backups(tmp_path):
    p = Project(path=str(tmp_path / "ep.vop"))
    assert backup.make_backup(p) is None                 # файла ещё нет
    p.lines.append(Line(0, 1, "раз"))
    p.save()
    b1 = backup.make_backup(p)
    assert b1 and b1.exists()
    assert backup.make_backup(p) is None                 # слишком часто
    p.lines.append(Line(1, 2, "два"))
    p.save()
    b2 = backup.make_backup(p, min_interval=0)
    items = backup.list_backups(p)
    assert [b.path for b in items][0] == b2 and items[-1].lines == 1
    st = backup.load_backup_state(b1)
    p.restore(st)
    assert [ln.text for ln in p.lines] == ["раз"]
    for _ in range(5):
        backup.make_backup(p, min_interval=0, keep=3)
    assert len(list(backup.backup_dir(p).glob("*.vop"))) == 3


def test_window_save_makes_backup_and_restore(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    (tmp_path / "v.mp4").write_bytes(b"")
    p = Project(video_path=str(tmp_path / "v.mp4"), path=str(tmp_path / "ep.vop"))
    p.lines.append(Line(0, 1, "старый текст"))
    p.save()
    w = MainWindow(AppSettings())
    w.set_project(p)
    p.lines[0].text = "новый текст"
    w.mark_dirty("x")
    assert w.save_project()
    assert len(backup.list_backups(p)) == 1
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    w.restore_backup()
    assert w.project.lines[0].text == "старый текст"
    w.undo()
    assert w.project.lines[0].text == "новый текст"
    w.dirty = False
    w.close()
    del app
