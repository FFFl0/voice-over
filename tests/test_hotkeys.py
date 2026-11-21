import os

from voiceover.core.hotkeys import conflicts, merged


def test_conflicts_and_merge():
    assert conflicts({"a": "F9", "b": "f9", "c": ""}) == [("f9", "a", "b")]
    assert merged({"a": "R", "b": "P"}, {"a": "F9", "zzz": "X"}) == {"a": "F9", "b": "P"}


def test_window_hotkeys(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.models import Line, Project
    from voiceover.core.settings import AppSettings
    from voiceover.ui.hotkeys_dialog import HotkeysDialog
    from voiceover.ui.main_window import MainWindow
    (tmp_path / "v.mp4").write_bytes(b"")
    p = Project(video_path=str(tmp_path / "v.mp4"), duration=10)
    p.lines.append(Line(1, 2, "раз"))
    w = MainWindow(AppSettings())
    w.set_project(p)
    w.timeline.canvas.setFocus()
    calls = []
    monkeypatch.setattr(w.player, "toggle", lambda: calls.append("toggle"))
    monkeypatch.setattr(w, "add_line", lambda: calls.append("add"))

    def press(key, mods=Qt.NoModifier):
        return w._hotkey(QKeyEvent(QEvent.KeyPress, key, mods))

    assert press(Qt.Key_Space) and calls == ["toggle"]
    assert press(Qt.Key_N) and calls[-1] == "add"
    # переназначаем: пуск — F9, новая реплика — Ctrl+Shift+N; меню «Экспорт» — F12
    dlg = HotkeysDialog(w)
    rows = {k: ed for k, _d, ed in dlg.rows}
    from PySide6.QtGui import QKeySequence
    rows["play"].setKeySequence(QKeySequence("F9"))
    rows["new_line"].setKeySequence(QKeySequence("Ctrl+Shift+N"))
    export_key = next(k for k in rows if "Экспорт" in k and "видео" not in k and "дорож" not in k
                      and "субтит" not in k)
    rows[export_key].setKeySequence(QKeySequence("F12"))
    dlg._ok()
    assert dlg.result() and w.settings.hotkeys["play"] == "F9"
    w.apply_hotkeys()
    calls.clear()
    assert not press(Qt.Key_Space) and press(Qt.Key_F9) and calls == ["toggle"]
    assert press(Qt.Key_N, Qt.ControlModifier | Qt.ShiftModifier) and calls[-1] == "add"
    assert w.a_export.shortcut().toString() == "F12"
    # конфликт не сохраняется
    dlg2 = HotkeysDialog(w)
    rows2 = {k: ed for k, _d, ed in dlg2.rows}
    monkeypatch.setattr("voiceover.ui.hotkeys_dialog.QMessageBox.warning", lambda *a, **k: None)
    rows2["fwd"].setKeySequence(QKeySequence("F9"))
    dlg2._ok()
    assert not dlg2.result()
    # «по умолчанию» возвращает всё
    dlg2._reset()
    dlg2._ok()
    assert w.settings.hotkeys == {}
    w.apply_hotkeys()
    assert w.a_export.shortcut().toString() == "Ctrl+E"
    w.dirty = False
    w.close()
    del app
