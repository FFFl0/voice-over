"""Редакция программы.

Сборка для магазинов (Steam) — `VO_STORE=1 pyinstaller voiceover.spec`: в неё кладётся
файл-метка `voiceover/store_edition`. В такой сборке выключено то, что нельзя использовать
в платном продукте, — локальный перевод NLLB-200 (лицензия модели CC-BY-NC 4.0)."""

from __future__ import annotations

import os
from pathlib import Path

_MARK = Path(__file__).with_name("store_edition")


def is_store() -> bool:
    return os.environ.get("VO_STORE") == "1" or _MARK.exists()


def licenses_dir() -> Path:
    """Папка с лицензиями: в сборке — внутри программы, при запуске из исходников — в репозитории."""
    import sys
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "licenses"
