"""Запуск приложения."""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
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
