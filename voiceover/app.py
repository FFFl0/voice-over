"""Запуск приложения."""

from __future__ import annotations

import sys
from pathlib import Path


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
        from .core.settings import data_dir
        log = open(data_dir() / "voiceover.log", "a", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def main(argv: list[str] | None = None) -> int:
    ensure_std_streams()
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
