"""Отсчёт и «REC» поверх видео: общее рисование для окна-наложения над основным видео
и для окна актёра.

Состояние: mode — "" (ничего), "prep" (до начала реплики value секунд), "speak" (идёт
реплика), "tail" (реплика кончилась, запись ещё идёт), "next" (пауза перед новым дублем)."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme

REC_RED = QColor("#ff4d4d")


def paint_cue(p: QPainter, r: QRectF, mode: str, value: float, elapsed: float = 0.0) -> None:
    if not mode:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    side = min(r.width(), r.height())
    recording = mode in ("prep", "speak", "tail")
    if recording:
        # красная рамка и значок REC
        bw = max(3.0, side / 90)
        p.setPen(QPen(REC_RED, bw))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2))
        f = theme.body_font(max(9.0, side / 40), QFont.Bold)
        p.setFont(f)
        pad = side / 30
        dot = side / 30
        blink = (elapsed * 2) % 2 < 1.4
        badge = QRectF(r.left() + pad, r.top() + pad, dot * 5.6, dot * 1.8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(badge, dot * 0.4, dot * 0.4)
        if blink:
            p.setBrush(REC_RED)
            p.drawEllipse(QPointF(badge.left() + dot * 0.95, badge.center().y()), dot * 0.45, dot * 0.45)
        p.setPen(QColor("white"))
        p.drawText(badge.adjusted(dot * 1.7, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "REC")
    c = r.center()
    if mode == "prep" and value > 0.05:
        n = math.ceil(value)
        frac = value - math.floor(value) if value % 1 else 1.0
        rad = side * 0.17
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(c, rad, rad)
        p.setPen(QPen(QColor("white"), max(3.0, rad / 10), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        ring = QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2)
        p.drawArc(ring, 90 * 16, int(-360 * 16 * frac))
        p.setFont(theme.display_font(rad * 0.9))
        p.setPen(QColor("white"))
        p.drawText(ring, Qt.AlignCenter, str(n))
    elif mode == "speak" and elapsed < 1.2:
        # «Говорите» вспыхивает на секунду в начале реплики
        alpha = int(255 * max(0.0, 1 - max(0.0, elapsed - 0.6) / 0.6))
        f = theme.display_font(side / 9)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        box = QRectF(r.left(), c.y() - side / 9, r.width(), side / 4.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(alpha * 0.5)))
        p.drawRect(box)
        col = QColor(REC_RED)
        col.setAlpha(alpha)
        p.setPen(col)
        p.drawText(box, Qt.AlignCenter, "Говорите")
    elif mode == "next":
        f = theme.display_font(side / 14)
        p.setFont(f)
        box = QRectF(r.left(), c.y() - side / 14, r.width(), side / 7)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRect(box)
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignCenter, "Следующий дубль…")
    p.restore()


class CueOverlay(QWidget):
    """Прозрачное окно поверх основного видео (видео рисуется в отдельном системном окне,
    поэтому обычный виджет поверх него не виден). Показывается только во время записи."""

    def __init__(self, target: QWidget, window: QWidget):
        super().__init__(window, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.target = target
        self.window_ = window
        self.mode, self.value, self.elapsed = "", 0.0, 0.0
        self._f = _Follow(self)
        window.installEventFilter(self._f)
        target.installEventFilter(self._f)

    def set_state(self, mode: str, value: float = 0.0, elapsed: float = 0.0) -> None:
        self.mode, self.value, self.elapsed = mode, value, elapsed
        show = bool(mode) and self.target.isVisible() and not self.window_.isMinimized()
        if show:
            self.follow()
            if not self.isVisible():
                self.show()
            self.update()
        elif self.isVisible():
            self.hide()

    def follow(self) -> None:
        tl = self.target.mapToGlobal(self.target.rect().topLeft())
        self.setGeometry(tl.x(), tl.y(), self.target.width(), self.target.height())

    def paintEvent(self, _):
        p = QPainter(self)
        paint_cue(p, QRectF(self.rect()), self.mode, self.value, self.elapsed)
        p.end()


class _Follow(QObject):
    def __init__(self, ov: CueOverlay):
        super().__init__(ov)
        self.ov = ov

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Move, QEvent.Resize, QEvent.Show) and self.ov.isVisible():
            self.ov.follow()
        elif ev.type() in (QEvent.Hide, QEvent.WindowStateChange) and self.ov.isVisible():
            if self.ov.window_.isMinimized() or not self.ov.target.isVisible():
                self.ov.hide()
        return False
