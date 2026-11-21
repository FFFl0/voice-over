"""Индикатор уровня микрофона: шкала в дБ, удержание пика и лампа перегруза."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from . import theme

FLOOR = -60.0
CLIP_DB = -0.5        # выше — считаем перегрузом (хрип)
DECAY = 24.0          # дБ в секунду — спад показаний
HOLD = 1.5            # сек — удержание отметки пика
GREEN_TO, YELLOW_TO = -12.0, -3.0
TICKS = (-48, -36, -24, -18, -12, -6, -3, 0)


def to_db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-6 else FLOOR


class LevelMeter(QWidget):
    clipReset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = FLOOR
        self.peak_db = FLOOR
        self._peak_t = 0.0
        self._t = time.monotonic()
        self.clipped = False
        self.setMinimumHeight(30)
        self.setToolTip("Уровень микрофона. Громкие места речи — до жёлтого (−12…−3 дБ). "
                        "Красная лампа справа — перегруз (хрип): отодвиньтесь или убавьте "
                        "чувствительность микрофона. Щелчок по лампе — сбросить.")

    def sizeHint(self):
        return QSize(300, 30)

    def set_level(self, peak: float) -> None:
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        db = max(FLOOR, to_db(peak))
        self.db = db if db > self.db else max(db, self.db - DECAY * dt)
        if db >= self.peak_db or now - self._peak_t > HOLD:
            self.peak_db, self._peak_t = db, now
        if db >= CLIP_DB:
            self.clipped = True
        self.update()

    def reset(self) -> None:
        self.db = self.peak_db = FLOOR
        self.clipped = False
        self.update()

    def _x(self, db: float, w: float) -> float:
        return (db - FLOOR) / -FLOOR * w

    def paintEvent(self, _):
        th = theme.current()
        p = QPainter(self)
        lamp = 34
        w = self.width() - lamp - 6
        bar = QRectF(0, 2, w, 12)
        p.fillRect(bar, QColor(th.rule))
        # сегменты: зелёный / жёлтый / красный, неактивные — тусклые
        zones = ((FLOOR, GREEN_TO, th.success), (GREEN_TO, YELLOW_TO, th.warn), (YELLOW_TO, 0.0, th.error))
        for a, b, col in zones:
            xa, xb = self._x(a, w), self._x(b, w)
            dim = QColor(col)
            dim.setAlpha(45)
            p.fillRect(QRectF(xa, bar.top(), xb - xa, bar.height()), dim)
            lit = min(xb, self._x(self.db, w))
            if lit > xa:
                p.fillRect(QRectF(xa, bar.top(), lit - xa, bar.height()), QColor(col))
        # пик
        if self.peak_db > FLOOR + 1:
            xp = self._x(self.peak_db, w)
            p.fillRect(QRectF(xp - 1, bar.top() - 1, 2, bar.height() + 2), QColor(th.ink))
        # деления
        p.setFont(theme.body_font(7.5, tabular=True))
        p.setPen(QColor(th.muted))
        for t in TICKS:
            x = self._x(t, w)
            p.fillRect(QRectF(x, bar.bottom(), 1, 3), QColor(th.muted))
            r = QRectF(x - 14, bar.bottom() + 2, 28, 12)
            if t == 0:
                r.moveRight(x + 1)
            p.drawText(r, Qt.AlignHCenter | Qt.AlignTop if t != 0 else Qt.AlignRight | Qt.AlignTop,
                       str(t))
        # лампа перегруза
        lr = QRectF(self.width() - lamp, 0, lamp, 16)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        on = QColor(th.error)
        if not self.clipped:
            on.setAlpha(50)
        p.setBrush(on)
        p.drawRoundedRect(lr, 3, 3)
        p.setPen(QColor(th.paper) if self.clipped else QColor(th.muted))
        p.setFont(theme.body_font(7.5, QFont.Bold))
        p.drawText(lr, Qt.AlignCenter, "CLIP")
        p.end()

    def mousePressEvent(self, e):
        if e.position().x() >= self.width() - 38:
            self.clipped = False
            self.clipReset.emit()
            self.update()
