"""Ритмо-лента под видео: текст реплик бежит справа налево и проходит через линию
синхрона ровно тогда, когда слово нужно произнести. Каждое слово растянуто на время
своего звучания, поэтому актёр видит и начало слов, и паузы, и долгие гласные."""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Project
from ..core.rythmo import word_times
from . import theme

ROW_H = 34
CUE_X = 0.25     # линия синхрона — четверть ширины от левого края


class RythmoBand(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.t = 0.0
        self.pps = 170.0          # пикселей в секунду
        self.playing = False
        self.rate = 1.0
        self._stamp = time.monotonic()
        self._timer = QTimer(self, interval=16, timeout=self.update)
        self._cache: dict[str, tuple] = {}
        self.setMinimumHeight(ROW_H * 2 + 8)
        self.setToolTip("Ритмо-лента: слово произносится, когда его начало пересекает "
                        "линию. Колесо мыши — масштаб, клик — перейти.")

    def sizeHint(self):
        return QSize(600, ROW_H * 2 + 8)

    # ------------------------------------------------------------ API
    def set_project(self, p: Optional[Project]) -> None:
        self.project = p
        self._cache.clear()
        self.update()

    def invalidate(self) -> None:
        self._cache.clear()
        self.update()

    def set_time(self, t: float) -> None:
        self.t = t
        self._stamp = time.monotonic()
        if not self.playing:
            self.update()

    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        self._stamp = time.monotonic()
        if playing and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def now(self) -> float:
        if not self.playing:
            return self.t
        # между редкими сигналами плеера время досчитываем сами — лента едет плавно
        return self.t + min(0.5, time.monotonic() - self._stamp) * self.rate

    # ------------------------------------------------------------ отрисовка
    def _words(self, ln):
        key = ln.id
        sig = (ln.start, ln.end, ln.text, len(ln.words))
        c = self._cache.get(key)
        if c is None or c[0] != sig:
            c = (sig, word_times(ln))
            self._cache[key] = c
        return c[1]

    def paintEvent(self, _):
        th = theme.current()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(th.paper))
        now = self.now()
        cue = w * CUE_X
        t0 = now - cue / self.pps
        t1 = now + (w - cue) / self.pps
        rows_end = [-1e9, -1e9]
        font = theme.display_font(theme.TEXT_XL if h > 80 else theme.TEXT_LG)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        if self.project:
            for ln in self.project.lines:
                if not ln.enabled or ln.end < t0 - 5:
                    continue
                if ln.start > t1:
                    break
                # две строки: одновременные реплики разных персонажей не накладываются
                row = 0 if ln.start >= rows_end[0] else (1 if ln.start >= rows_end[1] else 0)
                rows_end[row] = max(rows_end[row], ln.end + 0.15)
                if ln.end < t0:
                    continue
                ch = self.project.character(ln.character_id)
                col = QColor(ch.color) if ch else QColor(th.muted)
                y = 4 + row * ROW_H
                xa = cue + (ln.start - now) * self.pps
                xb = cue + (ln.end - now) * self.pps
                band = QColor(col)
                band.setAlpha(38)
                p.fillRect(QRectF(xa, y, xb - xa, ROW_H - 4), band)
                p.fillRect(QRectF(xa, y + ROW_H - 7, xb - xa, 3), col)
                p.fillRect(QRectF(xa, y, 2, ROW_H - 4), col)          # начало реплики
                for a, b, word in self._words(ln):
                    xw = cue + (a - now) * self.pps
                    span = max(4.0, (b - a) * self.pps)
                    if xw > w or xw + span < 0:
                        continue
                    nat = fm.horizontalAdvance(word)
                    gap = min(fm.horizontalAdvance(" "), span * 0.25)   # промежуток между словами
                    sx = max(0.45, min(2.6, (span - gap) / max(1.0, nat)))
                    past = b < now
                    active = a <= now <= b
                    c = QColor(th.ink if active else (th.muted if past else th.text))
                    if active:
                        c = QColor(theme.role_text(col.name()) if ch else th.ink)
                    p.save()
                    p.translate(xw, y + (ROW_H - 4) / 2 + fm.ascent() / 2 - 3)
                    p.scale(sx, 1.0)
                    p.setPen(c)
                    p.drawText(QPointF(0, 0), word)
                    p.restore()
        # линия синхрона
        p.fillRect(QRectF(cue - 1, 0, 2, h), QColor(th.accent))
        p.setPen(QPen(QColor(th.border), 1))
        p.drawLine(QPointF(0, h - 0.5), QPointF(w, h - 0.5))
        p.end()

    # ------------------------------------------------------------ мышь
    def wheelEvent(self, e):
        d = e.angleDelta().y()
        self.pps = max(60.0, min(600.0, self.pps * (1.15 if d > 0 else 1 / 1.15)))
        self.update()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            cue = self.width() * CUE_X
            self.seekRequested.emit(max(0.0, self.now() + (e.position().x() - cue) / self.pps))
