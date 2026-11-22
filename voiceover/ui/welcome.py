"""Стартовый экран: создание/открытие проекта и недавние проекты."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QStyledItemDelegate,
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


class RecentDelegate(QStyledItemDelegate):
    """Строка недавнего проекта: название и прогресс в одну линию, папка под ними."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 58)

    def paint(self, p: QPainter, option, index):
        from PySide6.QtWidgets import QStyle
        th = theme.current()
        r = option.rect.adjusted(14, 8, -14, -8)
        p.save()
        if option.state & QStyle.State_MouseOver or option.state & QStyle.State_Selected:
            p.fillRect(option.rect, QColor(th.paper3))
        missing = index.data(Qt.UserRole + 1) == "файл не найден"
        p.setFont(theme.body_font(theme.TEXT_MD, QFont.Medium))
        p.setPen(QColor(th.muted if missing else th.ink))
        info = index.data(Qt.UserRole + 1) or ""
        fm = p.fontMetrics()
        p.drawText(QRectF(r.left(), r.top(), r.width() - 190, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, r.width() - 200))
        p.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        p.setPen(QColor(th.muted))
        p.drawText(QRectF(r.right() - 190, r.top(), 190, 22), Qt.AlignRight | Qt.AlignVCenter, info)
        folder = str(Path(index.data(Qt.UserRole) or "").parent)
        p.drawText(QRectF(r.left(), r.top() + 22, r.width(), 20), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(folder, Qt.ElideMiddle, r.width()))
        p.setPen(QColor(th.rule))
        p.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        p.restore()


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

        # первый запуск: четыре шага столбцом (значок, шаг, что в нём делают)
        self.steps_box = QWidget()
        steps = QVBoxLayout(self.steps_box)
        steps.setContentsMargins(0, 0, 0, 0)
        steps.setSpacing(14)
        steps_title = QLabel("Как устроена работа")
        steps_title.setObjectName("itemTitle")
        steps.addWidget(steps_title)
        self._step_cards = []
        for ic, head, text in (
                ("film", "1. Видео", "Выберите фильм или серию"),
                ("subs", "2. Текст", "Импорт субтитров или распознавание Whisper"),
                ("mic", "3. Запись", "Актёры записывают роли под видео"),
                ("export", "4. Экспорт", "Видео с новой звуковой дорожкой")):
            row = QHBoxLayout()
            row.setSpacing(14)
            icl = QLabel()
            icl.setProperty("iconName", ic)
            icl.setFixedWidth(28)
            txt = QVBoxLayout()
            txt.setSpacing(2)
            h = QLabel(head)
            h.setObjectName("itemTitle")
            t = QLabel(text)
            t.setObjectName("muted")
            t.setWordWrap(True)
            txt.addWidget(h)
            txt.addWidget(t)
            row.addWidget(icl, 0, Qt.AlignTop)
            row.addLayout(txt, 1)
            steps.addLayout(row)
            self._step_cards.append(icl)
        steps.addStretch(1)

        # вернулись: главное — недавние проекты с прогрессом
        self.recent_box = QWidget()
        rb = QVBoxLayout(self.recent_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(8)
        self.recent_title = QLabel("Недавние проекты")
        self.recent_title.setObjectName("itemTitle")
        self.recent = QListWidget()
        self.recent.setWordWrap(True)
        self.recent.setObjectName("recent")
        self.recent.setItemDelegate(RecentDelegate(self.recent))
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.setMouseTracking(True)
        self.recent.setMinimumHeight(220)
        self.recent.itemActivated.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        self.recent.itemClicked.connect(lambda it: self.recentRequested.emit(it.data(Qt.UserRole)))
        rb.addWidget(self.recent_title)
        rb.addWidget(self.recent, 1)
        ver = QLabel(f"версия {__version__}")
        ver.setObjectName("muted")
        ver.setAlignment(Qt.AlignRight)

        # две колонки: слева кто мы и что начать, справа — с чем продолжить (или шаги)
        intro = QWidget()
        col = QVBoxLayout(intro)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        col.addWidget(self.logo)
        col.addSpacing(10)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(14)
        col.addLayout(btns)
        col.addStretch(1)
        right = QWidget()
        rc = QVBoxLayout(right)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.addWidget(self.recent_box)
        rc.addWidget(self.steps_box)
        body = QWidget()
        body.setMaximumWidth(1080)
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(64)
        bl.addWidget(intro, 5)
        bl.addWidget(right, 6)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(body, 8)
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
            it = QListWidgetItem(Path(p).stem)
            it.setData(Qt.UserRole, p)
            it.setData(Qt.UserRole + 1, project_summary(p))
            it.setToolTip(p)
            self.recent.addItem(it)
        self.recent_box.setVisible(bool(paths))
        self.steps_box.setVisible(not paths)
