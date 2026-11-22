"""Стартовый экран: создание/открытие проекта и недавние проекты."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from . import theme


def project_summary(path: str) -> str:
    """Короткая сводка проекта для списка недавних: прогресс записи."""
    import json
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "файл не найден"
    except Exception:  # noqa: BLE001
        return "не удалось прочитать"
    lines = [ln for ln in d.get("lines", []) if ln.get("enabled", True) and
             str(ln.get("text", "")).strip()]
    if not lines:
        return "реплик пока нет"
    done = sum(1 for ln in lines if ln.get("rec_path") and ln.get("review") != "redo")
    return f"записано {done} из {len(lines)} ({done * 100 // len(lines)}%)"


class WelcomePage(QWidget):
    newRequested = Signal()
    openRequested = Signal()
    recentRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignLeft)
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignLeft)
        sub = QLabel("Многоголосая озвучка фильмов, сериалов, мультфильмов и аниме.")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignLeft)

        self.btn_new = QPushButton("  Новый проект")
        self.btn_new.setProperty("primary", True)
        self.btn_new.setMinimumHeight(42)
        self.btn_open = QPushButton("  Открыть проект")
        self.btn_open.setMinimumHeight(42)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_open)
        btns.addStretch(1)

        steps = QHBoxLayout()
        steps.setContentsMargins(0, 0, 0, 0)
        steps.setSpacing(2)
        self._step_cards = []
        for ic, head, text in (
                ("film", "1. Видео", "Выберите фильм или серию"),
                ("subs", "2. Текст", "Импорт субтитров или распознавание Whisper"),
                ("mic", "3. Запись", "Актёры записывают роли под видео"),
                ("export", "4. Экспорт", "Видео с новой звуковой дорожкой")):
            card = QFrame()
            card.setObjectName("stepCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 14, 16, 14)
            icl = QLabel()
            icl.setProperty("iconName", ic)
            h = QLabel(head)
            h.setObjectName("itemTitle")
            t = QLabel(text)
            t.setObjectName("muted")
            t.setWordWrap(True)
            cl.addWidget(icl)
            cl.addWidget(h)
            cl.addWidget(t)
            card.setFixedWidth(200)
            card.setMinimumHeight(132)
            steps.addWidget(card)
            self._step_cards.append(icl)

        self.recent_title = QLabel("Недавние проекты")
        self.recent_title.setObjectName("label")
        self.recent = QListWidget()
        self.recent.setWordWrap(True)
        self.recent.setObjectName("recent")
        self.recent.setMinimumHeight(150)
        self.recent.itemActivated.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        self.recent.itemClicked.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        ver = QLabel(f"версия {__version__}")
        ver.setObjectName("muted")
        ver.setAlignment(Qt.AlignRight)

        # Одна колонка фиксированной ширины по центру; внутри всё выровнено влево
        body = QWidget()
        body.setMaximumWidth(860)
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        col.addWidget(self.logo)
        col.addSpacing(10)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(14)
        col.addLayout(btns)
        col.addSpacing(36)
        col.addLayout(steps)
        col.addSpacing(36)
        col.addWidget(self.recent_title)
        col.addWidget(self.recent)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(body, 6)
        center.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 16)
        lay.addStretch(2)
        lay.addLayout(center)
        lay.addStretch(3)
        lay.addWidget(ver)

        self.btn_new.clicked.connect(self.newRequested)
        self.btn_open.clicked.connect(self.openRequested)
        self.apply_icons()

    def apply_icons(self):
        t = theme.current()
        self.logo.setPixmap(theme.app_icon().pixmap(QSize(72, 72)))
        self.btn_new.setIcon(theme.icon("plus", t.accent_ink))
        self.btn_open.setIcon(theme.icon("open"))
        for lbl in self._step_cards:
            lbl.setPixmap(theme.icon(lbl.property("iconName"), t.ink2, 24).pixmap(24, 24))

    def set_recent(self, paths: list[str]):
        self.recent.clear()
        for p in paths[:6]:
            info = project_summary(p)
            it = QListWidgetItem(f"{Path(p).stem}   ·   {info}\n{Path(p).parent}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            if info == "файл не найден":
                it.setForeground(QColor(theme.current().muted))
            self.recent.addItem(it)
        self.recent.setVisible(bool(paths))
        self.recent_title.setVisible(bool(paths))
