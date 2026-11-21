"""Интерфейс: текст реплик не исполняется как HTML, ошибки видны, экспорт не затирает молча."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from voiceover.core.settings import AppSettings
    from voiceover.ui import theme
    from voiceover.ui.main_window import MainWindow
    theme.apply_theme(app, "dark")
    w = MainWindow(AppSettings())
    yield w
    w.dirty = False
    w.close()


HTML = '<img src="file:///etc/passwd"><b style="font-size:60pt">жирный</b>'


def test_line_text_is_plain(win, tmp_path):
    from voiceover.core.models import Line, Project
    p = Project(path=str(tmp_path / "p.vop"), duration=10)
    c = p.add_character("<i>Имя</i>")
    p.lines = [Line(0, 2, HTML, c.id)]
    win.set_project(p)
    win._update_now_line(1.0)
    assert win.now_lbl.textFormat() == Qt.PlainText and win.now_lbl.text() == HTML
    assert win.cue_who.textFormat() == Qt.PlainText
    win.select_line(p.lines[0].id)
    # суфлёр показывает пометки курсивом, но текст реплики экранирован
    assert "<img" not in win.record.prompt.text() and "&lt;img" in win.record.prompt.text()
    tip = win.lines_model.data(win.lines_model.index(0, 4), Qt.ToolTipRole)
    assert "<img" not in tip and "&lt;img" in tip


def test_excepthook_logs_and_shows(app, tmp_path, monkeypatch):
    from voiceover import app as appmod
    monkeypatch.setenv("APPDATA", str(tmp_path))
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    old = sys.excepthook
    try:
        appmod.install_excepthook()
        try:
            raise ValueError("сломалось")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = old
    assert shown and "сломалось" in shown[0] and "voiceover.log" in shown[0]
    log = (tmp_path / "VoiceOverStudio" / "voiceover.log").read_text(encoding="utf-8")
    assert "ValueError: сломалось" in log and "Traceback" in log


def test_export_asks_before_overwrite(win, tmp_path, monkeypatch):
    from voiceover.core.models import Project
    from voiceover.ui import main_window as mw
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    existing = tmp_path / "out.mkv"
    existing.write_bytes(b"old")
    p = Project(path=str(tmp_path / "p.vop"), video_path=str(video), duration=1)
    win.set_project(p)

    class Dlg:
        def __init__(self, default, parent):
            from PySide6.QtWidgets import QLineEdit, QComboBox
            self.out = QLineEdit(str(existing))
            self.kind = QComboBox()
            self.kind.addItem("v", "video")
            self.loudness = QComboBox()
            self.loudness.addItem("не менять", None)
            from PySide6.QtWidgets import QCheckBox
            self.logo, self.intro = QLineEdit(), QLineEdit()
            self.logo_pos = QComboBox()
            self.burn = QCheckBox()

        def exec(self):
            return QDialog.Accepted

        def options(self):
            from voiceover.core.ffmpeg import ExportOptions
            return ExportOptions()

    asked = []
    monkeypatch.setattr(mw, "ExportDialog", Dlg)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1]))
    started = []
    monkeypatch.setattr(win, "run_task", lambda *a, **k: started.append(a))
    win.export()
    assert any("уже существует" in q for q in asked) and not started
    assert existing.read_bytes() == b"old"


def test_subtitle_export_dialog_flow(win, tmp_path, monkeypatch):
    from voiceover.core.models import Line, Project
    from voiceover.core.subtitles import load_subtitles
    from voiceover.ui import dialogs
    (tmp_path / "ep01.mkv").write_bytes(b"x")       # видео есть — иначе модальный «Видео не найдено»
    p = Project(path=str(tmp_path / "ep01.vop"), video_path=str(tmp_path / "ep01.mkv"), duration=10)
    a = p.add_character("Тенма")
    p.lines = [Line(1, 2, "Привет", a.id, orig_text="Xin chào"), Line(3, 4, "Пока", a.id)]
    win.set_project(p)

    def run(fmt, text):
        def ex(self):
            self.fmt.setCurrentIndex(self.fmt.findData(fmt))
            self.text.setCurrentIndex(self.text.findData(text))
            return QDialog.Accepted
        monkeypatch.setattr(dialogs.SubtitleExportDialog, "exec", ex)
        win.export_script()

    run("ass", "both")
    out = tmp_path / "ep01.ru.ass"
    subs = load_subtitles(out)
    assert subs[0].text == "Привет Xin chào" and subs[0].speaker == "Тенма"
    run("srt", "orig")
    subs = load_subtitles(tmp_path / "ep01.ru.srt", detect_speakers=False)
    assert [s.text for s in subs] == ["Xin chào", "Пока"]


def test_cast_between_episodes(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from voiceover.core import cast
    from voiceover.core.models import Line, Project
    from voiceover.ui import main_window as mw
    monkeypatch.setattr(cast, "library_path", lambda: tmp_path / "cast.json")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("Inazuma", True)))
    # серия 1: настроенные голоса -> состав
    ep1 = Project(path=str(tmp_path / "e1.vop"), duration=5)
    t = ep1.add_character("Тенма", actor="Саша")
    ep1.add_character("Аки", actor="Маша", color="#123456")
    ep1.lines = [Line(0, 1, "a", t.id)]
    win.set_project(ep1)
    win.chars.save_cast()
    assert {c["name"] for c in cast.get_cast("Inazuma")} == {"Тенма", "Аки"}
    # серия 2: загрузили состав (без добавления недостающих), затем импорт субтитров
    ep2 = Project(path=str(tmp_path / "e2.vop"), duration=5)
    win.set_project(ep2)
    win.chars.load_cast()
    assert ep2.cast_name == "Inazuma" and ep2.characters == []
    srt = tmp_path / "e2.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nАки: Привет\n\n"
                   "2\n00:00:03,000 --> 00:00:04,000\nАки: Пока\n\n"
                   "3\n00:00:05,000 --> 00:00:06,000\nНовенький: Кто тут?\n\n"
                   "4\n00:00:07,000 --> 00:00:08,000\nНовенький: Я\n", encoding="utf-8")
    monkeypatch.setattr(mw.ImportSubsDialog, "exec", lambda self: QDialog.Accepted)
    win.import_subtitles(str(srt))
    aki = ep2.character_by_name("Аки")
    assert aki.actor == "Маша" and aki.color == "#123456"          # из состава
    assert ep2.character_by_name("Новенький").actor == ""            # нет в составе
    assert "Inazuma" in win.chars.cast_lbl.text()
