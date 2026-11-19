"""Стартовый экран: создание/открытие проекта и недавние проекты."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from . import theme


class WelcomePage(QWidget):
    newRequested = Signal()
    openRequested = Signal()
    recentRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Многоголосая озвучка фильмов, сериалов, мультфильмов и аниме")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignCenter)

        self.btn_new = QPushButton("  Новый проект")
        self.btn_new.setProperty("primary", True)
        self.btn_new.setMinimumHeight(42)
        self.btn_open = QPushButton("  Открыть проект")
        self.btn_open.setMinimumHeight(42)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_open)
        btns.addStretch(1)

        steps = QHBoxLayout()
        steps.setSpacing(12)
        self._step_cards = []
        for ic, head, text in (
                ("film", "1. Видео", "Выберите фильм или серию"),
                ("subs", "2. Текст", "Импорт субтитров или распознавание Whisper"),
                ("users", "3. Роли", "AI-голоса или живые актёры"),
                ("export", "4. Экспорт", "Видео с новой звуковой дорожкой")):
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 14, 16, 14)
            icl = QLabel()
            icl.setProperty("iconName", ic)
            h = QLabel(head)
            h.setObjectName("h2")
            t = QLabel(text)
            t.setObjectName("muted")
            t.setWordWrap(True)
            cl.addWidget(icl)
            cl.addWidget(h)
            cl.addWidget(t)
            card.setFixedWidth(190)
            steps.addWidget(card)
            self._step_cards.append(icl)

        self.recent_title = QLabel("Недавние проекты")
        self.recent_title.setObjectName("h2")
        self.recent = QListWidget()
        self.recent.setMaximumWidth(800)
        self.recent.setMinimumHeight(150)
        self.recent.itemActivated.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        self.recent.itemClicked.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        ver = QLabel(f"версия {__version__}")
        ver.setObjectName("muted")
        ver.setAlignment(Qt.AlignCenter)

        col = QVBoxLayout()
        col.setSpacing(14)
        col.addStretch(2)
        col.addWidget(self.logo)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(8)
        col.addLayout(btns)
        col.addSpacing(18)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addLayout(steps)
        row.addStretch(1)
        col.addLayout(row)
        col.addSpacing(18)
        rl = QVBoxLayout()
        rl.addWidget(self.recent_title)
        rl.addWidget(self.recent)
        rrow = QHBoxLayout()
        rrow.addStretch(1)
        rrow.addLayout(rl, 3)
        rrow.addStretch(1)
        col.addLayout(rrow)
        col.addStretch(3)
        col.addWidget(ver)
        lay = QVBoxLayout(self)
        lay.addLayout(col)

        self.btn_new.clicked.connect(self.newRequested)
        self.btn_open.clicked.connect(self.openRequested)
        self.apply_icons()

    def apply_icons(self):
        t = theme.current()
        self.logo.setPixmap(theme.app_icon().pixmap(QSize(84, 84)))
        self.btn_new.setIcon(theme.icon("plus", t.accent_text))
        self.btn_open.setIcon(theme.icon("open"))
        for lbl in self._step_cards:
            lbl.setPixmap(theme.icon(lbl.property("iconName"), t.accent, 26).pixmap(26, 26))

    def set_recent(self, paths: list[str]):
        self.recent.clear()
        for p in paths:
            it = QListWidgetItem(theme.icon("film", theme.current().muted),
                                 f"{Path(p).stem}     —     {Path(p).parent}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.recent.addItem(it)
        self.recent.setVisible(bool(paths))
        self.recent_title.setVisible(bool(paths))
