"""Таймлайн: волна оригинала, дорожки персонажей, блоки реплик, курсор."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollBar, QVBoxLayout, QWidget

from ..core.models import Project, line_status
from . import theme

RULER_H = 26
WAVE_H = 48
LANE_H = 30
LABEL_W = 150
BLOCK_RADIUS = 3   # небольшое скругление блоков реплик
EDGE_PX = 6
SCROLL_ZONE = 36   # px у края, где перетаскивание прокручивает таймлайн
MUTE_W = 26        # кнопка заглушения дорожки звука в колонке подписей

# дорожки звука: ключ → подпись
WAVE_TITLES = {"original": "оригинал", "vocals": "голоса", "background": "фон"}


class TimelineCanvas(QWidget):
    seekRequested = Signal(float)
    lineSelected = Signal(str)
    lineActivated = Signal(str)      # двойной клик — проиграть реплику
    lineEdited = Signal(str)         # тайминг изменён перетаскиванием
    viewChanged = Signal()
    muteToggled = Signal(str)        # ключ дорожки звука: original / vocals / background

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.pps = 60.0            # пикселей в секунду
        self.offset = 0.0          # секунда у левого края
        self.playhead = 0.0
        self.selected: set[str] = set()
        self.follow = True
        self.waves: list[tuple[str, Optional[np.ndarray]]] = [("original", None)]
        self.muted: set[str] = set()
        self.peaks_rate = 50
        self.duration = 0.0
        self._mouse_x = 0.0
        self._autoscroll = QTimer(self, interval=16, timeout=self._autoscroll_tick)
        self._rec_peaks: dict[str, tuple[float, np.ndarray]] = {}
        self.preview_range: Optional[tuple[float, float]] = None
        self._hover: Optional[str] = None
        self._drag: Optional[tuple] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(RULER_H + WAVE_H + LANE_H * 2)

    # ---------------------------------------------------------- геометрия
    @property
    def peaks(self) -> Optional[np.ndarray]:   # волна первой дорожки (совместимость)
        return self.waves[0][1] if self.waves else None

    @peaks.setter
    def peaks(self, pk: Optional[np.ndarray]) -> None:
        self.waves = [("original", pk)]

    @property
    def lanes_top(self) -> int:
        return RULER_H + WAVE_H * max(1, len(self.waves))

    def wave_rect(self, i: int) -> QRectF:
        return QRectF(LABEL_W, RULER_H + i * WAVE_H, self.width() - LABEL_W, WAVE_H)

    def mute_rect(self, i: int) -> QRectF:
        return QRectF(LABEL_W - MUTE_W - 8, RULER_H + i * WAVE_H + (WAVE_H - MUTE_W) / 2,
                      MUTE_W, MUTE_W)

    def lanes(self) -> list[tuple[Optional[str], str, QColor, bool]]:
        out: list[tuple[Optional[str], str, QColor, bool]] = []
        if self.project:
            for c in self.project.characters:
                out.append((c.id, c.name, QColor(c.color), False))
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

    def set_waves(self, keys: list[str]) -> None:
        """Какие дорожки звука показывать (волны подгружаются потом через set_wave)."""
        old = dict(self.waves)
        self.waves = [(k, old.get(k)) for k in keys] or [("original", None)]
        self.setMinimumHeight(self.lanes_top + LANE_H * 2)
        self.updateGeometry()
        self.update()

    def set_wave(self, key: str, peaks: Optional[np.ndarray], rate: int) -> None:
        self.waves = [(k, peaks if k == key else pk) for k, pk in self.waves]
        self.peaks_rate = rate
        self.update()

    REC_PEAKS_PER_SEC = 100

    def rec_peaks(self, path) -> Optional[np.ndarray]:
        """Пики записи для волны внутри блока (кэш по времени изменения файла)."""
        import os
        key = str(path)
        try:
            mtime = os.path.getmtime(key)
        except OSError:
            return None
        c = self._rec_peaks.get(key)
        if c and c[0] == mtime:
            return c[1]
        try:
            import soundfile as sf
            data, sr = sf.read(key, dtype="float32", always_2d=True)
        except Exception:  # noqa: BLE001
            return None
        mono = np.abs(data).max(axis=1)
        hop = max(1, sr // self.REC_PEAKS_PER_SEC)
        n = len(mono) // hop
        pk = mono[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0, np.float32)
        top = float(pk.max()) if len(pk) else 0.0
        pk = pk / top if top > 0 else pk
        self._rec_peaks[key] = (mtime, pk)
        return pk

    def _draw_rec_wave(self, p: QPainter, ln, r: QRectF, color: QColor) -> None:
        src = self.project.resolve(ln.rec_path) if (self.project and ln.rec_path) else None
        if src is None:
            return
        pk = self.rec_peaks(src)
        if pk is None or not len(pk):
            return
        rate = self.REC_PEAKS_PER_SEC
        dur = len(pk) / rate
        a = ln.trim_in
        b = max(a, dur - ln.trim_out)
        t0 = ln.start + ln.rec_offset   # начало файла на шкале фильма
        xa = max(r.left(), self.x_of(t0 + a), LABEL_W)
        xb = min(r.right(), self.x_of(t0 + b), self.width())
        if xb - xa < 2:
            return
        mid = r.center().y()
        amp = r.height() / 2 - 2
        pen = QPen(color, 1)
        p.setPen(pen)
        x = xa
        while x < xb:
            t = self.t_of(x) - t0
            i0 = int(t * rate)
            i1 = max(i0 + 1, int((t + 2 / self.pps) * rate))
            seg = pk[max(0, i0):min(len(pk), i1)]
            if len(seg):
                v = float(seg.max())
                # фейды на волне — как они прозвучат
                if ln.fade_in > 0:
                    v *= min(1.0, max(0.0, (t - a) / ln.fade_in))
                if ln.fade_out > 0:
                    v *= min(1.0, max(0.0, (b - t) / ln.fade_out))
                h = max(0.5, v * amp)
                p.drawLine(QPointF(x, mid - h), QPointF(x, mid + h))
            x += 2

    def set_muted(self, muted: set[str]) -> None:
        self.muted = set(muted)
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
        p.setRenderHint(QPainter.Antialiasing, False)   # сетка и волна чёткими пикселями
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

        # дорожки звука: оригинал или, после отделения, голоса и фон
        for wi, (key, pk_arr) in enumerate(self.waves):
            wr = self.wave_rect(wi)
            p.fillRect(wr, QColor(th.lane_alt if wi % 2 == 0 else th.lane))
            if wi:
                p.setPen(QPen(QColor(th.border), 1))
                p.drawLine(QPointF(LABEL_W, wr.top() + 0.5), QPointF(w, wr.top() + 0.5))
            if pk_arr is None or not len(pk_arr):
                continue
            mid = wr.top() + WAVE_H / 2
            amp = WAVE_H / 2 - 4
            px = np.arange(LABEL_W, w, 2, dtype=np.float64)
            t0 = self.offset + (px - LABEL_W) / self.pps
            t1 = t0 + 2 / self.pps
            i0 = np.clip((t0 * self.peaks_rate).astype(int), 0, len(pk_arr))
            i1 = np.clip((t1 * self.peaks_rate).astype(int) + 1, 0, len(pk_arr))
            pk = pk_arr.astype(np.float32)
            vals = np.array([pk[a:b].max() if b > a else 0.0 for a, b in zip(i0, i1)])
            vals = np.sqrt(vals)  # визуально ровнее для тихих участков
            wc = QColor(th.wave)
            if key in self.muted:
                wc.setAlpha(60)   # заглушённая дорожка — бледная волна
            p.setPen(QPen(wc, 2, Qt.SolidLine, Qt.FlatCap))
            for x, v in zip(px, vals):
                if v > 0.01:
                    hh = max(1.0, v * amp)
                    p.drawLine(QPointF(x, mid - hh), QPointF(x, mid + hh))

        # реплики
        if self.project:
            idx = self.lane_index()
            fm = p.fontMetrics()
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
                # Квадратный блок цвета роли. Готовая реплика — сплошная заливка,
                # реплика без актуального звука — только контур со штриховкой.
                ready = st in ("ok", "rec")
                path = QPainterPath()
                path.addRoundedRect(r, BLOCK_RADIUS, BLOCK_RADIUS)
                p.setRenderHint(QPainter.Antialiasing, True)
                fill = QColor(col)
                fill.setAlpha(255 if (sel or hover) else 215)
                if not ln.enabled:
                    fill.setAlpha(40)
                p.setPen(Qt.NoPen)
                if ready or not ln.enabled:
                    p.fillPath(path, fill)
                else:
                    ghost = QColor(col)
                    ghost.setAlpha(60 if not (sel or hover) else 95)
                    p.fillPath(path, ghost)
                    p.fillPath(path, QBrush(QColor(col), Qt.BDiagPattern))
                    p.setPen(QPen(col, 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
                if sel:   # SELECT_OUTLINE: рамка акцента снаружи блока + светлая внутренняя
                    p.setRenderHint(QPainter.Antialiasing, True)
                    outer = QPainterPath()
                    outer.addRoundedRect(r.adjusted(-3, -3, 3, 3), 5, 5)
                    p.setPen(QPen(QColor(th.accent), 2.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(outer)
                    inner = QPainterPath()
                    inner.addRoundedRect(r.adjusted(1, 1, -1, -1), 2, 2)
                    p.setPen(QPen(QColor(th.text), 1))
                    p.drawPath(inner)
                p.setRenderHint(QPainter.Antialiasing, False)
                if ready and ln.enabled and r.width() > 8:
                    wc = QColor(th.on_role)
                    wc.setAlpha(70)
                    self._draw_rec_wave(p, ln, r, wc)
                if r.width() > 34:
                    p.setPen(QColor(th.on_role) if (ready and ln.enabled) else QColor(th.ink))
                    p.drawText(r.adjusted(8, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm.elidedText(ln.text, Qt.ElideRight, int(r.width() - 14)))

        # линейка
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor(th.paper))
        p.setPen(QPen(QColor(th.border), 1))
        p.drawLine(QPointF(0, RULER_H - 0.5), QPointF(w, RULER_H - 0.5))
        if self.preview_range:
            a, b = self.preview_range
            xa, xb = max(LABEL_W, self.x_of(a)), self.x_of(b)
            if xb > xa:
                p.fillRect(QRectF(xa, RULER_H - 3, xb - xa, 2), QColor(th.accent2))
        p.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
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
        p.fillRect(QRectF(0, RULER_H, LABEL_W, h - RULER_H), QColor(th.paper))
        p.setPen(QPen(QColor(th.border), 1))
        p.drawLine(QPointF(LABEL_W - 0.5, 0), QPointF(LABEL_W - 0.5, h))
        p.setFont(theme.body_font(theme.TEXT_SM, QFont.Medium))
        fm = p.fontMetrics()
        for wi, (key, _pk) in enumerate(self.waves):
            y = RULER_H + wi * WAVE_H
            muted = key in self.muted
            p.setPen(QColor(th.muted))
            p.setFont(theme.body_font(theme.TEXT_SM, QFont.Medium))
            p.drawText(QRectF(14, y, LABEL_W - MUTE_W - 26, WAVE_H), Qt.AlignVCenter,
                       WAVE_TITLES.get(key, key))
            mr = self.mute_rect(wi)
            p.setRenderHint(QPainter.Antialiasing, True)
            if muted:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(th.error))
                p.drawRoundedRect(mr, 3, 3)
            else:
                p.setPen(QPen(QColor(th.border), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(mr.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            p.setRenderHint(QPainter.Antialiasing, False)
            ic = theme.icon("mute" if muted else "volume",
                            th.paper if muted else th.muted, 16)
            ic.paint(p, mr.adjusted(5, 5, -5, -5).toRect())
        small = theme.body_font(theme.TEXT_SM - 1)
        for i, (_, name, col, is_rec) in enumerate(lanes):
            y = self.lanes_top + i * LANE_H
            p.fillRect(QRectF(0, y, 4, LANE_H), col)          # ключ цвета роли
            p.setFont(theme.body_font(theme.TEXT_SM, QFont.Medium))
            p.setPen(QColor(th.ink))
            name_w = LABEL_W - 22 - (44 if is_rec else 0)
            p.drawText(QRectF(14, y, name_w, LANE_H), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, name_w))
            if is_rec:
                p.setFont(small)
                p.setPen(QColor(th.muted))
                p.drawText(QRectF(LABEL_W - 52, y, 44, LANE_H), Qt.AlignVCenter | Qt.AlignRight,
                           "запись")

        # курсор
        x = self.x_of(self.playhead)
        if x >= LABEL_W:
            pc = QColor(th.accent)
            p.setRenderHint(QPainter.Antialiasing, False)
            p.fillRect(QRectF(round(x) - 1, RULER_H - 6, 2, h), pc)
            p.fillRect(QRectF(round(x) - 5, 3, 10, RULER_H - 9), pc)
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
        for wi, (key, _pk) in enumerate(self.waves):
            if self.mute_rect(wi).adjusted(-3, -3, 3, 3).contains(pos):
                self.muteToggled.emit(key)
                return
        self._mouse_x = pos.x()
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
        self._drag = (part, ln, self.t_of(pos.x()), ln.start, ln.end, False, pos.x())
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
            on_mute = any(self.mute_rect(i).contains(pos) for i in range(len(self.waves)))
            if on_mute:
                self.setCursor(Qt.PointingHandCursor)
                self.setToolTip("Заглушить / включить дорожку")
                return
            if ln:
                ch = self.project.character(ln.character_id) if self.project else None
                from html import escape
                self.setToolTip(f"<b>{escape(ch.name) if ch else 'без роли'}</b> "
                                f"{ln.start:.2f}–{ln.end:.2f} с<br>{escape(ln.text)}")
            else:
                self.setToolTip("")
            return
        self._mouse_x = pos.x()
        self._apply_drag(pos.x())
        self._update_autoscroll()

    def _apply_drag(self, x: float) -> None:
        """Применить перетаскивание к точке x (вызывается и при автопрокрутке)."""
        if self._drag is None:
            return
        x = min(max(LABEL_W, x), self.width())   # за краем — край, дальше прокрутка
        if self._drag[0] == "seek":
            t = max(0.0, self.t_of(x))
            if self.duration:
                t = min(t, self.duration)
            self.playhead = t
            self.seekRequested.emit(t)
            self.update()
            return
        part, ln, t_press, s0, e0, moved, x0 = self._drag
        if abs(x - x0) < 3 and not moved:
            return
        # время, а не пиксели: при прокрутке во время перетаскивания блок едет вместе с ней
        dt = self.t_of(x) - t_press
        if part == "move":
            dt = max(-s0, dt)
            ln.start, ln.end = s0 + dt, e0 + dt
        elif part == "left":
            ln.start = min(max(0.0, s0 + dt), ln.end - 0.1)
        else:
            ln.end = max(e0 + dt, ln.start + 0.1)
        self._drag = (part, ln, t_press, s0, e0, True, x0)
        self.update()

    def _edge_speed(self) -> float:
        """Секунд в секунду: <0 — влево, >0 — вправо, 0 — не у края."""
        x, w = self._mouse_x, self.width()
        vis = self.visible_seconds()
        if x > w - SCROLL_ZONE:
            k = min(3.0, (x - (w - SCROLL_ZONE)) / SCROLL_ZONE)
        elif x < LABEL_W + SCROLL_ZONE:
            k = -min(3.0, (LABEL_W + SCROLL_ZONE - x) / SCROLL_ZONE)
        else:
            return 0.0
        return k * vis * 0.6

    def _update_autoscroll(self) -> None:
        if self._drag is not None and self._edge_speed() != 0.0:
            if not self._autoscroll.isActive():
                self._autoscroll.start()
        else:
            self._autoscroll.stop()

    def _autoscroll_tick(self) -> None:
        if self._drag is None:
            self._autoscroll.stop()
            return
        v = self._edge_speed()
        if v == 0.0:
            self._autoscroll.stop()
            return
        max_off = max(0.0, self.duration - self.visible_seconds()) if self.duration else None
        new = self.offset + v * self._autoscroll.interval() / 1000
        new = max(0.0, new if max_off is None else min(max_off, new))
        if abs(new - self.offset) < 1e-9:
            return
        self.offset = new
        self.viewChanged.emit()
        self._apply_drag(self._mouse_x)

    def leaveEvent(self, e):
        if self._hover:
            self._hover = None
            self.update()

    def mouseReleaseEvent(self, e):
        self._autoscroll.stop()
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
        self.canvas.duration = self.duration
        self.refresh()

    def set_duration(self, d: float) -> None:
        self.duration = max(self.duration, d)
        self.canvas.duration = self.duration
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
