import pytest


@pytest.fixture(autouse=True)
def _offline_dictionaries(monkeypatch):
    """Тесты окна не должны скачивать словари орфографии из интернета:
    уже лежащие в папке словари (как в test_spell) работают, загрузка — нет."""
    from voiceover.core import spell
    orig = spell.ensure_dictionary

    def offline(lang, folder=None):
        name = spell.LANGS[lang][1]
        d = folder or spell.dict_dir()
        if all((d / f"{name}{ext}").exists() for ext in (".aff", ".dic")):
            return orig(lang, folder)
        raise OSError("словари в тестах не скачиваются")
    monkeypatch.setattr(spell, "ensure_dictionary", offline)


def pytest_sessionfinish(session, exitstatus):
    """Закрыть и удалить окна Qt до выхода интерпретатора: иначе Qt удаляет десятки
    оставшихся от тестов окон в случайном порядке и может упасть уже после тестов."""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        try:
            if hasattr(w, "dirty"):
                w.dirty = False
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
