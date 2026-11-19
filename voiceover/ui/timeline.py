"""Таймлайн: дорожки персонажей, блоки реплик, курсор воспроизведения."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QScrollBar, QVBoxLayout, QWidget

from ..core.models import Project, line_status

RULER_H = 20
LANE_H = 26
LABEL_W = 110
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
        self._drag: Optional[tuple] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(RULER_H + LANE_H * 2)

    # ---------------------------------------------------------- геометрия
    def lanes(self) -> list[tuple[Optional[str], str, QColor]]:
        out: list[tuple[Optional[str], str, QColor]] = []
        if self.project:
            for c in self.project.characters:
                out.append((c.id, c.name, QColor(c.color)))
            if any(ln.character_id is None or self.project.character(ln.character_id) is None
                   for ln in self.project.lines):
                out.append((None, "— без роли —", QColor("#888888")))
        return out

    def lane_index(self) -> dict[Optional[str], int]:
        return {cid: i for i, (cid, _, _) in enumerate(self.lanes())}

    def x_of(self, t: float) -> float:
        return LABEL_W + (t - self.offset) * self.pps

    def t_of(self, x: float) -> float:
        return self.offset + (x - LABEL_W) / self.pps

    def visible_seconds(self) -> float:
        return max(1.0, (self.width() - LABEL_W) / self.pps)

    def line_rect(self, ln, lane: int) -> QRectF:
        x0 = self.x_of(ln.start)
        x1 = self.x_of(ln.end)
        y = RULER_H + lane * LANE_H + 2
        return QRectF(x0, y, max(3.0, x1 - x0), LANE_H - 4)

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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        pal = self.palette()
        bg = pal.base().color()
        p.fillRect(self.rect(), bg)
        lanes = self.lanes()
        w = self.width()

        # дорожки
        for i, (_, name, col) in enumerate(lanes):
            y = RULER_H + i * LANE_H
            band = QColor(pal.alternateBase().color() if i % 2 else bg)
            p.fillRect(QRectF(LABEL_W, y, w - LABEL_W, LANE_H), band)

        # линейка
        p.fillRect(QRectF(0, 0, w, RULER_H), pal.window())
        step = self._tick_step()
        t = int(self.offset / step) * step
        p.setPen(pal.text().color())
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        end_t = self.t_of(w)
        grid = QColor(pal.mid().color())
        grid.setAlpha(60)
        while t <= end_t:
            x = self.x_of(t)
            if x >= LABEL_W:
                p.setPen(pal.text().color())
                p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
                m, s = divmod(t, 60)
                lbl = f"{int(m)}:{s:04.1f}" if step < 1 else f"{int(m)}:{int(s):02d}"
                p.drawText(QPointF(x + 3, RULER_H - 7), lbl)
                p.setPen(grid)
                p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
            t += step

        # реплики
        if self.project:
            idx = self.lane_index()
            t0, t1 = self.offset, end_t
            for ln in self.project.lines:
                if ln.end < t0 or ln.start > t1:
                    continue
                ch = self.project.character(ln.character_id)
                cid = ch.id if ch else None
                lane = idx.get(cid)
                if lane is None:
                    continue
                r = self.line_rect(ln, lane)
                col = QColor(ch.color) if ch else QColor("#888")
                st = line_status(self.project, ln)
                if not ln.enabled:
                    col.setAlpha(50)
                elif st in ("none", "nochar"):
                    col.setAlpha(110)
                p.setPen(Qt.NoPen)
                brush = QBrush(col, Qt.SolidPattern if st not in ("stale",) else Qt.Dense3Pattern)
                p.setBrush(brush)
                p.drawRect(r)
                if ln.id in self.selected:
                    p.setPen(QPen(pal.highlight().color(), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(r.adjusted(1, 1, -1, -1))
                if r.width() > 30:
                    p.setPen(QColor("#000") if col.lightness() > 130 else QColor("#fff"))
                    mark = {"rec": "● ", "none": "○ ", "stale": "↻ "}.get(st, "")
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               p.fontMetrics().elidedText(mark + ln.text, Qt.ElideRight,
                                                          int(r.width() - 6)))

        # подписи дорожек
        p.fillRect(QRectF(0, RULER_H, LABEL_W, self.height()), pal.window())
        for i, (_, name, col) in enumerate(lanes):
            y = RULER_H + i * LANE_H
            p.fillRect(QRectF(4, y + 7, 10, 10), col)
            p.setPen(pal.text().color())
            p.drawText(QRectF(18, y, LABEL_W - 20, LANE_H), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, LABEL_W - 22))

        # курсор
        x = self.x_of(self.playhead)
        if x >= LABEL_W:
            p.setPen(QPen(QColor("#ff3b30"), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    def _tick_step(self) -> float:
        for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if s * self.pps >= 70:
                return s
        return 1200

    # ---------------------------------------------------------- мышь
    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() != Qt.LeftButton:
            return
        if pos.y() < RULER_H or pos.x() < LABEL_W:
            self.seekRequested.emit(max(0.0, self.t_of(pos.x())))
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
            _, part = self.hit(pos)
            self.setCursor(Qt.SizeHorCursor if part in ("left", "right") else Qt.ArrowCursor)
            return
        if self._drag[0] == "seek":
            self.seekRequested.emit(max(0.0, self.t_of(pos.x())))
            return
        part, ln, x0, s0, e0, _ = self._drag
        dt = (pos.x() - x0) / self.pps
        if abs(pos.x() - x0) < 3 and not self._drag[5]:
            return
        if part == "move":
            dt = max(-s0, dt)
            ln.start, ln.end = s0 + dt, e0 + dt
        elif part == "left":
            ln.start = min(max(0.0, s0 + dt), ln.end - 0.1)
        else:
            ln.end = max(e0 + dt, ln.start + 0.1)
        self._drag = (part, ln, x0, s0, e0, True)
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
        from PySide6.QtCore import QSize
        return QSize(800, RULER_H + LANE_H * max(3, len(self.lanes())))


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
        self.duration = project.duration if project else 0.0
        self.refresh()

    def set_duration(self, d: float) -> None:
        self.duration = max(self.duration, d)
        self._sync_scroll()

    def refresh(self) -> None:
        self.canvas.setMinimumHeight(RULER_H + LANE_H * max(2, len(self.canvas.lanes())))
        self._sync_scroll()
        self.canvas.update()

    def _sync_scroll(self):
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(0.0, self.duration - self.canvas.visible_seconds()) * 10))
        self.scroll.setPageStep(int(self.canvas.visible_seconds() * 10))
        self.scroll.setValue(int(self.canvas.offset * 10))
        self.scroll.blockSignals(False)

    def _on_scroll(self, v: int):
        self.canvas.offset = v / 10
        self.canvas.update()
