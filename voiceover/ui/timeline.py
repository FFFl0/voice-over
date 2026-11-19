"""Таймлайн: волна оригинала, дорожки персонажей, блоки реплик, курсор."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QScrollBar, QVBoxLayout, QWidget

from ..core.models import LineSource, Project, line_status
from . import theme

RULER_H = 24
WAVE_H = 44
LANE_H = 30
LABEL_W = 130
EDGE_PX = 6


class TimelineCanvas(QWidget):
    seekRequested = Signal(float)
    lineSelected = Signal(str)
    lineActivated = Signal(str)      # двойной клик — проиграть реплику
    lineEdited = Signal(str)         # тайминг изменён перетаскиванием
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.pps = 60.0            # пикселей в секунду
        self.offset = 0.0          # секунда у левого края
        self.playhead = 0.0
        self.selected: set[str] = set()
        self.follow = True
        self.peaks: Optional[np.ndarray] = None
        self.peaks_rate = 50
        self.preview_range: Optional[tuple[float, float]] = None
        self._hover: Optional[str] = None
        self._drag: Optional[tuple] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(RULER_H + WAVE_H + LANE_H * 2)

    # ---------------------------------------------------------- геометрия
    @property
    def lanes_top(self) -> int:
        return RULER_H + WAVE_H

    def lanes(self) -> list[tuple[Optional[str], str, QColor, bool]]:
        out: list[tuple[Optional[str], str, QColor, bool]] = []
        if self.project:
            for c in self.project.characters:
                out.append((c.id, c.name, QColor(c.color), c.source == LineSource.RECORDING))
            if any(ln.character_id is None or self.project.character(ln.character_id) is None
                   for ln in self.project.lines):
                out.append((None, "без роли", QColor(theme.current().muted), False))
        return out

    def lane_index(self) -> dict[Optional[str], int]:
        return {cid: i for i, (cid, *_rest) in enumerate(self.lanes())}

    def x_of(self, t: float) -> float:
        return LABEL_W + (t - self.offset) * self.pps

    def t_of(self, x: float) -> float:
        return self.offset + (x - LABEL_W) / self.pps

    def visible_seconds(self) -> float:
        return max(1.0, (self.width() - LABEL_W) / self.pps)

    def line_rect(self, ln, lane: int) -> QRectF:
        x0 = self.x_of(ln.start)
        x1 = self.x_of(ln.end)
        y = self.lanes_top + lane * LANE_H + 3
        return QRectF(x0, y, max(4.0, x1 - x0), LANE_H - 6)

    def hit(self, pos: QPointF):
        if not self.project:
            return None, None
        idx = self.lane_index()
        for ln in reversed(self.project.lines):
            cid = ln.character_id if self.project.character(ln.character_id) else None
            if cid not in idx:
                continue
            r = self.line_rect(ln, idx[cid])
            if r.adjusted(-2, 0, 2, 0).contains(pos):
                if pos.x() <= r.left() + EDGE_PX and r.width() > EDGE_PX * 2:
                    return ln, "left"
                if pos.x() >= r.right() - EDGE_PX and r.width() > EDGE_PX * 2:
                    return ln, "right"
                return ln, "move"
        return None, None

    # ---------------------------------------------------------- API
    def set_peaks(self, peaks: Optional[np.ndarray], rate: int) -> None:
        self.peaks = peaks
        self.peaks_rate = rate
        self.update()

    def set_playhead(self, t: float) -> None:
        self.playhead = t
        if self.follow and self._drag is None:
            vis = self.visible_seconds()
            if t < self.offset or t > self.offset + vis * 0.9:
                self.offset = max(0.0, t - vis * 0.1)
                self.viewChanged.emit()
        self.update()

    def ensure_visible(self, start: float, end: float) -> None:
        vis = self.visible_seconds()
        if start < self.offset or end > self.offset + vis:
            self.offset = max(0.0, start - vis * 0.2)
            self.viewChanged.emit()
        self.update()

    def zoom(self, factor: float, anchor_x: Optional[float] = None) -> None:
        if anchor_x is None:
            anchor_x = self.x_of(self.playhead)
        t = self.t_of(anchor_x)
        self.pps = max(2.0, min(800.0, self.pps * factor))
        self.offset = max(0.0, t - (anchor_x - LABEL_W) / self.pps)
        self.viewChanged.emit()
        self.update()

    # ---------------------------------------------------------- отрисовка
    def paintEvent(self, _):
        th = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        lanes = self.lanes()
        end_t = self.t_of(w)
        p.fillRect(self.rect(), QColor(th.lane))

        # полосы дорожек
        for i in range(len(lanes)):
            y = self.lanes_top + i * LANE_H
            if i % 2:
                p.fillRect(QRectF(LABEL_W, y, w - LABEL_W, LANE_H), QColor(th.lane_alt))

        # сетка
        step = self._tick_step()
        grid = QColor(th.border)
        grid.setAlpha(110)
        t = int(self.offset / step) * step
        while t <= end_t:
            x = self.x_of(t)
            if x >= LABEL_W:
                p.setPen(QPen(grid, 1))
                p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
            t += step

        # волна оригинала
        p.fillRect(QRectF(LABEL_W, RULER_H, w - LABEL_W, WAVE_H), QColor(th.lane_alt))
        if self.peaks is not None and len(self.peaks):
            mid = RULER_H + WAVE_H / 2
            amp = WAVE_H / 2 - 4
            px = np.arange(LABEL_W, w, 2, dtype=np.float64)
            t0 = self.offset + (px - LABEL_W) / self.pps
            t1 = t0 + 2 / self.pps
            i0 = np.clip((t0 * self.peaks_rate).astype(int), 0, len(self.peaks))
            i1 = np.clip((t1 * self.peaks_rate).astype(int) + 1, 0, len(self.peaks))
            pk = self.peaks.astype(np.float32)
            vals = np.array([pk[a:b].max() if b > a else 0.0 for a, b in zip(i0, i1)])
            vals = np.sqrt(vals)  # визуально ровнее для тихих участков
            p.setPen(QPen(QColor(th.wave), 2, Qt.SolidLine, Qt.FlatCap))
            for x, v in zip(px, vals):
                if v > 0.01:
                    hh = max(1.0, v * amp)
                    p.drawLine(QPointF(x, mid - hh), QPointF(x, mid + hh))

        # реплики
        if self.project:
            idx = self.lane_index()
            fm = p.fontMetrics()
            sc = theme.status_colors()
            for ln in self.project.lines:
                if ln.end < self.offset or ln.start > end_t:
                    continue
                ch = self.project.character(ln.character_id)
                lane = idx.get(ch.id if ch else None)
                if lane is None:
                    continue
                r = self.line_rect(ln, lane)
                col = QColor(ch.color) if ch else QColor(th.muted)
                st = line_status(self.project, ln)
                sel = ln.id in self.selected
                hover = ln.id == self._hover
                fill = QColor(col)
                if th.dark:
                    fill.setAlpha(230 if sel else (170 if hover else 120))
                else:
                    fill.setAlpha(245 if sel else (220 if hover else 190))
                if not ln.enabled:
                    fill.setAlpha(35)
                path = QPainterPath()
                path.addRoundedRect(r, 5, 5)
                p.setPen(Qt.NoPen)
                p.fillPath(path, fill)
                if st in ("none", "stale") and ln.enabled:
                    hatch = QColor(th.text)
                    hatch.setAlpha(40)
                    p.fillPath(path, QBrush(hatch, Qt.BDiagPattern))
                # полоса цвета роли слева
                p.save()
                p.setClipPath(path)
                p.fillRect(QRectF(r.left(), r.top(), 3, r.height()), col)
                p.restore()
                if sel:
                    p.setPen(QPen(QColor(th.text), 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
                # статус: точка справа
                if r.width() > 18 and ln.enabled:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(sc.get(st, th.muted)))
                    p.drawEllipse(QPointF(r.right() - 7, r.top() + 7), 3, 3)
                if r.width() > 34:
                    txt_col = QColor("#ffffff") if (th.dark or col.lightness() < 150) \
                        else QColor("#111111")
                    p.setPen(txt_col)
                    prefix = "● " if st == "rec" else ""
                    p.drawText(r.adjusted(8, 0, -14, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm.elidedText(prefix + ln.text, Qt.ElideRight, int(r.width() - 22)))

        # линейка
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor(th.panel))
        p.setPen(QPen(QColor(th.border), 1))
        p.drawLine(QPointF(0, RULER_H - 0.5), QPointF(w, RULER_H - 0.5))
        if self.preview_range:
            a, b = self.preview_range
            xa, xb = max(LABEL_W, self.x_of(a)), self.x_of(b)
            if xb > xa:
                pc = QColor(th.accent)
                pc.setAlpha(70)
                p.fillRect(QRectF(xa, RULER_H - 5, xb - xa, 4), pc)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        t = int(self.offset / step) * step
        while t <= end_t:
            x = self.x_of(t)
            if x >= LABEL_W:
                p.setPen(QPen(QColor(th.muted), 1))
                p.drawLine(QPointF(x, RULER_H - 7), QPointF(x, RULER_H - 1))
                m, s = divmod(t, 60)
                hh, m = divmod(int(m), 60)
                lbl = (f"{hh}:{m:02d}:{int(s):02d}" if hh else
                       (f"{m}:{s:04.1f}" if step < 1 else f"{m}:{int(s):02d}"))
                p.drawText(QPointF(x + 4, RULER_H - 9), lbl)
            t += step

        # левая колонка: подписи дорожек
        p.fillRect(QRectF(0, RULER_H, LABEL_W, h - RULER_H), QColor(th.panel))
        p.setPen(QPen(QColor(th.border), 1))
        p.drawLine(QPointF(LABEL_W - 0.5, 0), QPointF(LABEL_W - 0.5, h))
        p.setFont(self.font())
        fm = p.fontMetrics()
        p.setPen(QColor(th.muted))
        p.drawText(QRectF(12, RULER_H, LABEL_W - 16, WAVE_H), Qt.AlignVCenter, "Оригинал")
        for i, (_, name, col, is_rec) in enumerate(lanes):
            y = self.lanes_top + i * LANE_H
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(10, y + LANE_H / 2 - 5, 10, 10), 3, 3)
            p.setPen(QColor(th.text))
            label = ("🎙 " if is_rec else "") + name
            p.drawText(QRectF(28, y, LABEL_W - 32, LANE_H), Qt.AlignVCenter,
                       fm.elidedText(label, Qt.ElideRight, LABEL_W - 34))

        # курсор
        x = self.x_of(self.playhead)
        if x >= LABEL_W:
            pc = QColor(th.danger)
            p.setPen(QPen(pc, 1.5))
            p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, h))
            p.setPen(Qt.NoPen)
            p.setBrush(pc)
            p.drawPolygon(QPolygonF([QPointF(x - 6, 2), QPointF(x + 6, 2),
                                     QPointF(x + 6, RULER_H - 10), QPointF(x, RULER_H - 4),
                                     QPointF(x - 6, RULER_H - 10)]))
        p.end()

    def _tick_step(self) -> float:
        for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if s * self.pps >= 80:
                return s
        return 1200

    # ---------------------------------------------------------- мышь
    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < self.lanes_top or pos.x() < LABEL_W:
            self.seekRequested.emit(max(0.0, self.t_of(max(LABEL_W, pos.x()))))
            self._drag = ("seek",)
            return
        ln, part = self.hit(pos)
        if ln is None:
            self.seekRequested.emit(max(0.0, self.t_of(pos.x())))
            self._drag = ("seek",)
            return
        if not (e.modifiers() & Qt.ControlModifier):
            self.selected = {ln.id}
        else:
            self.selected ^= {ln.id}
        self.lineSelected.emit(ln.id)
        self._drag = (part, ln, pos.x(), ln.start, ln.end, False)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            ln, part = self.hit(pos)
            hid = ln.id if ln else None
            if hid != self._hover:
                self._hover = hid
                self.update()
            self.setCursor(Qt.SizeHorCursor if part in ("left", "right") else
                           (Qt.PointingHandCursor if ln else Qt.ArrowCursor))
            if ln:
                ch = self.project.character(ln.character_id) if self.project else None
                self.setToolTip(f"<b>{ch.name if ch else 'без роли'}</b> "
                                f"{ln.start:.2f}–{ln.end:.2f} с<br>{ln.text}")
            else:
                self.setToolTip("")
            return
        if self._drag[0] == "seek":
            self.seekRequested.emit(max(0.0, self.t_of(max(LABEL_W, pos.x()))))
            return
        part, ln, x0, s0, e0, moved = self._drag
        if abs(pos.x() - x0) < 3 and not moved:
            return
        dt = (pos.x() - x0) / self.pps
        if part == "move":
            dt = max(-s0, dt)
            ln.start, ln.end = s0 + dt, e0 + dt
        elif part == "left":
            ln.start = min(max(0.0, s0 + dt), ln.end - 0.1)
        else:
            ln.end = max(e0 + dt, ln.start + 0.1)
        self._drag = (part, ln, x0, s0, e0, True)
        self.update()

    def leaveEvent(self, e):
        if self._hover:
            self._hover = None
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] != "seek" and self._drag[5]:
            ln = self._drag[1]
            ln.start = round(ln.start, 3)
            ln.end = round(ln.end, 3)
            self.lineEdited.emit(ln.id)
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        ln, _ = self.hit(e.position())
        if ln is not None:
            self.lineActivated.emit(ln.id)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.25 if d > 0 else 0.8, e.position().x())
        else:
            self.offset = max(0.0, self.offset - d / 120 * self.visible_seconds() * 0.15)
            self.viewChanged.emit()
            self.update()
        e.accept()

    def sizeHint(self):
        return QSize(800, self.lanes_top + LANE_H * max(3, len(self.lanes())))


class Timeline(QWidget):
    """Холст + горизонтальная прокрутка."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        self.scroll = QScrollBar(Qt.Horizontal, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.scroll)
        self.duration = 0.0
        self.canvas.viewChanged.connect(self._sync_scroll)
        self.scroll.valueChanged.connect(self._on_scroll)

    def set_project(self, project: Optional[Project]) -> None:
        self.canvas.project = project
        self.canvas.peaks = None
        self.canvas.preview_range = None
        self.canvas.selected = set()
        self.duration = project.duration if project else 0.0
        self.refresh()

    def set_duration(self, d: float) -> None:
        self.duration = max(self.duration, d)
        self._sync_scroll()

    def refresh(self) -> None:
        self.canvas.setMinimumHeight(self.canvas.lanes_top + LANE_H * max(2, len(self.canvas.lanes())))
        self._sync_scroll()
        self.canvas.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_scroll()

    def _sync_scroll(self):
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(0.0, self.duration - self.canvas.visible_seconds()) * 10))
        self.scroll.setPageStep(int(self.canvas.visible_seconds() * 10))
        self.scroll.setValue(int(self.canvas.offset * 10))
        self.scroll.blockSignals(False)

    def _on_scroll(self, v: int):
        self.canvas.offset = v / 10
        self.canvas.update()
