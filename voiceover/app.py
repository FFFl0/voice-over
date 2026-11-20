"""Запуск приложения."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt


def ensure_std_streams() -> None:
    """Без консоли (pythonw.exe, собранный .exe) sys.stdout/sys.stderr равны None, и любая
    библиотека, которая печатает прогресс (скачивание моделей через huggingface_hub/tqdm,
    vosk-tts, demucs), падает с «'NoneType' object has no attribute 'write'».
    Направляем вывод в журнал программы и отключаем полосы прогресса загрузчиков."""
    import os
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log = open(log_path(), "a", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def log_path() -> Path:
    from .core.settings import data_dir
    return data_dir() / "voiceover.log"


def write_log(text: str) -> None:
    import datetime
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except Exception:  # noqa: BLE001
        pass


_in_hook = False


def install_excepthook() -> None:
    """Непредвиденная ошибка в интерфейсе: подробности — в журнал, пользователю — окно,
    а не молчаливо «ничего не происходит» (без консоли ошибку иначе не видно)."""
    import threading
    import traceback

    def hook(exc_type, exc, tb):
        global _in_hook
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        write_log(details)
        try:
            sys.__stderr__ and sys.__stderr__.write(details)
        except Exception:  # noqa: BLE001
            pass
        if _in_hook or issubclass(exc_type, KeyboardInterrupt):
            return
        _in_hook = True
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                box = QMessageBox(QMessageBox.Critical, "Непредвиденная ошибка",
                                  f"{exc_type.__name__}: {exc}\n\nРабота программы продолжится. "
                                  f"Подробности записаны в журнал:\n{log_path()}")
                box.setTextFormat(Qt.PlainText)
                box.setDetailedText(details)
                box.exec()
        finally:
            _in_hook = False

    sys.excepthook = hook
    threading.excepthook = lambda a: write_log(
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))


def main(argv: list[str] | None = None) -> int:
    ensure_std_streams()
    install_excepthook()
    argv = list(sys.argv if argv is None else argv)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .core.models import PROJECT_EXT
    from .core.settings import AppSettings
    from .ui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VoiceOverStudio")
    from .ui import theme

    settings = AppSettings.load()
    theme.apply_theme(app, settings.theme)
    app.setWindowIcon(theme.app_icon())
    win = MainWindow(settings)
    win.show()
    # Открыть проект/видео, переданный в командной строке (или перетащенный на .exe)
    if len(argv) > 1:
        p = Path(argv[1])
        if p.suffix.lower() == PROJECT_EXT:
            win.open_project(str(p), ask=False)
        elif p.is_file():
            win.new_project(str(p))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
