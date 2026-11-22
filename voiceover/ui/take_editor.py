"""Визуальная правка дубля: волна записи на фоне волны оригинала и границ реплики.

Мышью: края — обрезка начала и конца, квадратики сверху — нарастание и затухание,
перетаскивание самой волны — сдвиг записи относительно реплики (синхрон).
Двойной клик по ручке сбрасывает её. Колесо — масштаб."""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import soundfile as sf
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Line, Project
from . import theme

RATE = 200          # пиков в секунду
HANDLE = 7          # px — зона захвата края
FADE_BOX = 10       # px — квадратик фейда
MIN_KEEP = 0.05     # сек — меньше дубль не обрезаем


def _peaks(x: np.ndarray, sr: int) -> np.ndarray:
    if x.ndim > 1:
        x = np.abs(x).max(axis=1)
    else:
        x = np.abs(x)
    hop = max(1, sr // RATE)
    n = len(x) // hop
    return x[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0, np.float32)


class TakeEditor(QWidget):
    changed = Signal()            # во время перетаскивания (обновить поля)
    edited = Signal(str)          # перетаскивание закончено: подпись для истории
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.line: Optional[Line] = None
        self.color = QColor("#63bde7")
        self.take: Optional[np.ndarray] = None     # пики дубля (норм.)
        self.dur = 0.0
        self.ref: Optional[np.ndarray] = None      # пики оригинала на видимом участке
        self.ref_t0 = 0.0
        self._key = None
        self._drag: Optional[tuple] = None
        self._hover = ""
        self.zoom = 1.0
        self.playhead: Optional[float] = None
        self.setMouseTracking(True)
        self.setMinimumHeight(110)
        self.setToolTip("Тяните края волны — обрезка, квадратики сверху — нарастание и "
                        "затухание, саму волну — сдвиг записи. Двойной клик по ручке — сброс. "
                        "Колесо — масштаб, клик по пустому месту — перейти к моменту видео.")

    def sizeHint(self):
        return QSize(600, 190)

    # ------------------------------------------------------------ данные
    def set_line(self, project: Optional[Project], line: Optional[Line], color: str = "") -> None:
        self.project, self.line = project, line
        if color:
            self.color = QColor(color)
        path = project.resolve(line.rec_path) if (project and line and line.rec_path) else None
        key = None
        if path and path.exists():
            key = (str(path), os.path.getmtime(path))
        if key != self._key:
            self._key = key
            self.take, self.dur = None, 0.0
            if key:
                try:
                    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
                    pk = _peaks(data, sr)
                    top = float(pk.max()) if len(pk) else 0.0
                    self.take = pk / top if top > 0 else pk
                    self.dur = len(data) / sr
                except Exception:  # noqa: BLE001
                    self.take = None
            self.zoom = 1.0
        self._load_ref()
        self.update()

    def _load_ref(self) -> None:
        """Волна оригинала (голоса после отделения или весь звук) вокруг реплики."""
        self.ref = None
        p, ln = self.project, self.line
        if not p or not ln:
            return
        src = p.project_dir / "vocals.wav"
        if not src.exists():
            src = p.resolve(p.original_wav)
        if not src or not src.exists():
            return
        a = max(0.0, min(ln.start, ln.start + ln.rec_offset) - 3.0)
        b = max(ln.end, ln.start + ln.rec_offset + self.dur) + 3.0
        try:
            with sf.SoundFile(str(src)) as f:
                sr = f.samplerate
                f.seek(min(f.frames, int(a * sr)))
                data = f.read(max(0, int((b - a) * sr)), dtype="float32", always_2d=True)
        except Exception:  # noqa: BLE001
            return
        pk = _peaks(data, sr)
        top = float(np.percentile(pk, 99)) if len(pk) else 0.0
        self.ref = np.clip(pk / top, 0, 1) if top > 0 else pk
        self.ref_t0 = a

    def set_playhead(self, t: Optional[float]) -> None:
        self.playhead = t
        self.update()

    # ------------------------------------------------------------ геометрия
    def _file_t0(self) -> float:
        return self.line.start + self.line.rec_offset

    def _range(self) -> tuple[float, float]:
        ln = self.line
        a = min(ln.start, self._file_t0()) - 0.4
        b = max(ln.end, self._file_t0() + self.dur) + 0.4
        mid, half = (a + b) / 2, (b - a) / 2 / self.zoom
        return mid - half, mid + half

    def _x(self, t: float) -> float:
        a, b = self._range()
        return 12 + (t - a) / max(1e-6, b - a) * (self.width() - 24)

    def _t(self, x: float) -> float:
        a, b = self._range()
        return a + (x - 12) / max(1.0, self.width() - 24) * (b - a)

    def _kept(self) -> tuple[float, float]:
        t0 = self._file_t0()
        return t0 + self.line.trim_in, t0 + self.dur - self.line.trim_out

    def _hit(self, pos: QPointF) -> str:
        if self.take is None or not self.line:
            return ""
        ka, kb = self._kept()
        xa, xb = self._x(ka), self._x(kb)
        top = 18
        fi = self._x(ka + self.line.fade_in)
        fo = self._x(kb - self.line.fade_out)
        if abs(pos.y() - top) <= FADE_BOX:
            if abs(pos.x() - fi) <= FADE_BOX:
                return "fade_in"
            if abs(pos.x() - fo) <= FADE_BOX:
                return "fade_out"
        if pos.y() > top - FADE_BOX:
            if abs(pos.x() - xa) <= HANDLE:
                return "trim_in"
            if abs(pos.x() - xb) <= HANDLE:
                return "trim_out"
            if xa < pos.x() < xb:
                return "move"
        return ""

    # ------------------------------------------------------------ отрисовка
    def paintEvent(self, _):
        th = theme.current()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(th.paper2))
        if not self.line:
            p.end()
            return
        ln = self.line
        top, bot = 18, h - 22
        mid = (top + bot) / 2
        amp = (bot - top) / 2 - 2
        # границы реплики
        la, lb = self._x(ln.start), self._x(ln.end)
        band = QColor(self.color)
        band.setAlpha(34)
        p.fillRect(QRectF(la, top - 8, lb - la, bot - top + 16), band)
        p.setPen(QPen(self.color, 1, Qt.DashLine))
        p.drawLine(QPointF(la, top - 8), QPointF(la, bot + 8))
        p.drawLine(QPointF(lb, top - 8), QPointF(lb, bot + 8))
        # волна оригинала — бледно, снизу вверх от середины
        if self.ref is not None and len(self.ref):
            rc = QColor(th.muted)
            rc.setAlpha(70)
            p.setPen(QPen(rc, 1))
            x = 12.0
            while x < w - 12:
                t = self._t(x) - self.ref_t0
                i = int(t * RATE)
                j = max(i + 1, int((self._t(x + 2) - self.ref_t0) * RATE))
                seg = self.ref[max(0, i):min(len(self.ref), j)]
                if len(seg):
                    hh = float(seg.max()) * amp
                    p.drawLine(QPointF(x, mid - hh), QPointF(x, mid + hh))
                x += 2
        if self.take is None:
            p.setPen(QColor(th.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "Записи нет — запишите дубль")
            self._labels(p, la, lb, h)
            p.end()
            return
        t0 = self._file_t0()
        ka, kb = self._kept()
        col_on = QColor(self.color)
        col_off = QColor(th.muted)
        col_off.setAlpha(110)
        x = max(12.0, self._x(t0))
        x_end = min(w - 12.0, self._x(t0 + self.dur))
        while x < x_end:
            t = self._t(x)
            i, j = int((t - t0) * RATE), int((self._t(x + 2) - t0) * RATE)
            seg = self.take[max(0, i):min(len(self.take), max(i + 1, j))]
            if len(seg):
                v = float(seg.max())
                kept = ka <= t <= kb
                if kept:
                    if ln.fade_in > 0:
                        v *= min(1.0, max(0.0, (t - ka) / ln.fade_in))
                    if ln.fade_out > 0:
                        v *= min(1.0, max(0.0, (kb - t) / ln.fade_out))
                p.setPen(QPen(col_on if kept else col_off, 2 if kept else 1))
                hh = max(0.5, v * amp)
                p.drawLine(QPointF(x, mid - hh), QPointF(x, mid + hh))
            x += 2
        # затемнение обрезанного
        shade = QColor(th.paper)
        shade.setAlpha(140)
        xa, xb = self._x(ka), self._x(kb)
        p.fillRect(QRectF(self._x(t0), top, xa - self._x(t0), bot - top), shade)
        p.fillRect(QRectF(xb, top, self._x(t0 + self.dur) - xb, bot - top), shade)
        # линии фейдов
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(th.ink), 1.2))
        fi, fo = self._x(ka + ln.fade_in), self._x(kb - ln.fade_out)
        path = QPainterPath(QPointF(xa, bot))
        path.lineTo(QPointF(fi, top))
        path.lineTo(QPointF(fo, top))
        path.lineTo(QPointF(xb, bot))
        p.drawPath(path)
        # ручки обрезки
        acc = QColor(th.accent)
        for xx, key in ((xa, "trim_in"), (xb, "trim_out")):
            wdt = 3 if self._hover == key or (self._drag and self._drag[0] == key) else 2
            p.fillRect(QRectF(xx - wdt / 2, top - 4, wdt, bot - top + 8), acc)
            p.fillRect(QRectF(xx - 5 if key == "trim_in" else xx - 1, bot - 4, 6, 10), acc)
        for xx, key in ((fi, "fade_in"), (fo, "fade_out")):
            r = QRectF(xx - FADE_BOX / 2, top - FADE_BOX / 2, FADE_BOX, FADE_BOX)
            p.setBrush(acc if self._hover == key else QColor(th.paper))
            p.setPen(QPen(acc, 1.5))
            p.drawRect(r)
        p.setBrush(Qt.NoBrush)
        if self.playhead is not None and 12 <= self._x(self.playhead) <= w - 12:
            p.fillRect(QRectF(self._x(self.playhead) - 1, 4, 2, h - 8), QColor(th.ink))
        self._labels(p, la, lb, h)
        # подписи значений
        p.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        p.setPen(QColor(th.muted))
        txt = (f"обрезка {ln.trim_in:.2f} / {ln.trim_out:.2f} с   "
               f"сдвиг {ln.rec_offset:+.2f} с   звучит {max(0.0, kb - ka):.2f} с")
        p.drawText(QRectF(12, h - 18, w - 24, 16), Qt.AlignRight | Qt.AlignVCenter, txt)
        p.end()

    def _labels(self, p: QPainter, la: float, lb: float, h: int) -> None:
        th = theme.current()
        p.setFont(theme.body_font(theme.TEXT_SM, QFont.Medium))
        p.setPen(QColor(theme.role_text(self.color.name())))
        p.drawText(QRectF(la + 4, 0, max(60.0, lb - la), 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "реплика")
        p.setPen(QColor(th.muted))
        p.drawText(QRectF(12, h - 18, 300, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "серым — голос оригинала")

    # ------------------------------------------------------------ мышь
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.line:
            return
        part = self._hit(e.position())
        ln = self.line
        if not part:
            self.seekRequested.emit(max(0.0, self._t(e.position().x())))
            return
        self._drag = (part, self._t(e.position().x()),
                      (ln.trim_in, ln.trim_out, ln.fade_in, ln.fade_out, ln.rec_offset), False)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            hv = self._hit(pos)
            if hv != self._hover:
                self._hover = hv
                self.update()
            self.setCursor({"trim_in": Qt.SizeHorCursor, "trim_out": Qt.SizeHorCursor,
                            "fade_in": Qt.SizeHorCursor, "fade_out": Qt.SizeHorCursor,
                            "move": Qt.OpenHandCursor}.get(hv, Qt.ArrowCursor))
            return
        part, t_press, (ti, to, fi, fo, off), _moved = self._drag
        ln = self.line
        dt = self._t(pos.x()) - t_press
        snap = 0.01
        if part == "trim_in":
            ln.trim_in = round(min(max(0.0, ti + dt), self.dur - ln.trim_out - MIN_KEEP), 3)
            ln.fade_in = min(ln.fade_in, self._kept()[1] - self._kept()[0])
        elif part == "trim_out":
            ln.trim_out = round(min(max(0.0, to - dt), self.dur - ln.trim_in - MIN_KEEP), 3)
            ln.fade_out = min(ln.fade_out, self._kept()[1] - self._kept()[0])
        elif part in ("fade_in", "fade_out"):
            keep = max(0.0, self._kept()[1] - self._kept()[0])
            if part == "fade_in":
                ln.fade_in = round(min(max(0.0, fi + dt), keep - ln.fade_out, 5.0), 2)
            else:
                ln.fade_out = round(min(max(0.0, fo - dt), keep - ln.fade_in, 5.0), 2)
        else:
            ln.rec_offset = round(round((off + dt) / snap) * snap, 3)
            self.setCursor(Qt.ClosedHandCursor)
        self._drag = (part, t_press, (ti, to, fi, fo, off), True)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[3]:
            labels = {"trim_in": "Обрезка дубля", "trim_out": "Обрезка дубля",
                      "fade_in": "Нарастание", "fade_out": "Затухание", "move": "Сдвиг записи"}
            if self._drag[0] == "move":
                self._load_ref()
            self.edited.emit(labels[self._drag[0]])
        self._drag = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, e):
        part = self._hit(e.position())
        ln = self.line
        if not ln or part not in ("trim_in", "trim_out", "fade_in", "fade_out"):
            return
        setattr(ln, part, 0.0)
        self.changed.emit()
        self.edited.emit("Сброс правки дубля")
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        self.zoom = max(1.0, min(20.0, self.zoom * (1.25 if d > 0 else 0.8)))
        self.update()
        e.accept()
