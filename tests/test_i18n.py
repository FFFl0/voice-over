import os

from voiceover import i18n


def test_core_translation():
    try:
        assert i18n.set_language("en") == "en"
        tr = i18n.tr
        assert tr("Реплики") == "Lines"
        assert tr(" Добавить") == " Add"                                  # пробел сохраняется
        assert tr("Удалено реплик: 5. Ctrl+Z — вернуть.") == "Lines deleted: 5. Ctrl+Z — bring back."
        assert tr("Отменить: Запись реплики") == "Undo: Line recording"   # вложенный перевод
        assert tr("Наруто") == "Наруто"                                   # данные не трогаем
        assert tr("Субтитры (*.ass *.srt);;Все файлы (*)") == "Subtitles (*.ass *.srt);;All files (*)"
        assert tr("Ошибка\n\nНет звука оригинала") == "Error\n\nNo original audio"
        assert tr(i18n.NoTr("Русский")) == "Русский"
        assert i18n.set_language("uk") == "uk" and i18n.tr("Запись") == "Запис"
        assert i18n.set_language("xx") == "ru" and i18n.tr("Запись") == "Запись"
    finally:
        i18n.set_language("ru")


def test_dictionaries_cover_the_ui():
    """Все строки интерфейса, найденные в коде, есть в обоих словарях."""
    import json
    from pathlib import Path
    en = json.loads(Path("voiceover/i18n/en.json").read_text(encoding="utf-8"))
    uk = json.loads(Path("voiceover/i18n/uk.json").read_text(encoding="utf-8"))
    assert set(en) == set(uk) and len(en) > 800
    for d in (en, uk):
        for k, v in d.items():
            assert k.count("{}") == v.count("{}"), k


def test_window_switches_language(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from voiceover.ui import i18n_qt
    try:
        i18n_qt.install(app, "en")
        from voiceover.core.models import Line, Project
        from voiceover.core.settings import AppSettings
        from voiceover.ui.main_window import MainWindow
        w = MainWindow(AppSettings())
        (tmp_path / "v.mp4").write_bytes(b"")
        p = Project(video_path=str(tmp_path / "v.mp4"), duration=10)
        p.lines.append(Line(1, 2, "Привет", p.add_character("Наруто").id))
        w.set_project(p)
        w.show()
        app.processEvents()
        assert w.tabs.tabText(0) == "Lines" and w.tabs.tabText(2) == "Recording"
        menus = [a.text() for a in w.menuBar().actions()]
        assert menus[0] == "&File" and "&Process" in menus
        assert w.b_next_todo.text() == " Record next"
        assert w.lines_model.headerData(4, Qt.Horizontal) == "Text"
        assert w.a_export.text() == "Export"
        assert w.search.placeholderText() == "Search text…"
        shown = []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(
            lambda *a, **k: shown.append(a[2])))
        from voiceover.ui import i18n_qt as iq
        iq._wrap_args(QMessageBox, "information", static=True)
        QMessageBox.information(w, "Экспорт", "Файл сохранён:\nC:/x.mkv")
        assert shown == ["File saved:\nC:/x.mkv"]
        w.statusBar().showMessage("Удалено реплик: 2. Ctrl+Z — вернуть.")
        assert w.statusBar().currentMessage() == "Lines deleted: 2. Ctrl+Z — bring back."
        assert w.lines_model.data(w.lines_model.index(0, 4)) == "Привет"     # текст реплики — как есть
        i18n_qt.set_language("uk")
        assert w.tabs.tabText(0) == "Репліки" and w.b_next_todo.text() == " Записати наступну"
        i18n_qt.set_language("ru")
        assert w.tabs.tabText(0) == "Реплики" and w.menuBar().actions()[0].text() == "&Файл"
        assert w.a_export.text() == "Экспорт"
        w.dirty = False
        w.close()
    finally:
        i18n_qt.set_language("ru")
    del app
