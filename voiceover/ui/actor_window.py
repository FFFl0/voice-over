"""Окно для актёра: видео, отсчёт, суфлёр и ритмо-лента — на втором мониторе или во весь
экран, пока режиссёр работает в основном окне. Кадры берутся у основного видео."""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .cue import paint_cue
from .record_panel import prompt_html
from .rythmo import RythmoBand


class FrameView(QWidget):
    """Кадр видео (с сохранением пропорций) и отсчёт поверх него."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: Optional[QImage] = None
        self.mode, self.value, self.elapsed = "", 0.0, 0.0
        self.setMinimumSize(320, 180)

    def set_frame(self, frame) -> None:
        try:
            if frame is None or not frame.isValid():
                return
            img = frame.toImage()
        except Exception:  # noqa: BLE001
            return
        if not img.isNull():
            self.image = img
            self.update()

    def set_cue(self, mode: str, value: float, elapsed: float) -> None:
        self.mode, self.value, self.elapsed = mode, value, elapsed
        self.update()

    def video_rect(self) -> QRectF:
        r = QRectF(self.rect())
        if self.image is None or self.image.isNull():
            return r
        iw, ih = self.image.width(), self.image.height()
        k = min(r.width() / iw, r.height() / ih)
        w, h = iw * k, ih * k
        return QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        vr = self.video_rect()
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(vr, self.image)
        paint_cue(p, vr, self.mode, self.value, self.elapsed)
        p.end()


class ActorWindow(QWidget):
    closed = Signal()

    def __init__(self, main):
        super().__init__(None, Qt.Window)
        self.main = main
        self.setWindowTitle("Окно актёра — VoiceOver Studio")
        self.setObjectName("actorWindow")
        self.view = FrameView()
        self.who = QLabel()
        self.who.setObjectName("cueWho")
        self.prompt = QLabel()
        self.prompt.setTextFormat(Qt.RichText)    # текст экранирован в prompt_html
        self.prompt.setWordWrap(True)
        self.prompt.setObjectName("cueText")
        self.next_lbl = QLabel()
        self.next_lbl.setTextFormat(Qt.PlainText)
        self.next_lbl.setObjectName("muted")
        self.rythmo = RythmoBand()
        self.rythmo.setMinimumHeight(90)
        self.btn_full = QPushButton("Во весь экран (F11)")
        self.btn_full.clicked.connect(self.toggle_full)
        top = QHBoxLayout()
        top.addWidget(self.who, 1)
        top.addWidget(self.btn_full)
        text = QVBoxLayout()
        text.setContentsMargins(24, 10, 24, 14)
        text.addLayout(top)
        text.addWidget(self.prompt)
        text.addWidget(self.next_lbl)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.rythmo)
        lay.addLayout(text)
        for seq in ("F11", "Escape"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(self.toggle_full if seq == "F11" else self._esc)
        self._sink = None
        self._t0 = time.monotonic()
        self._font_timer = QTimer(self, singleShot=True, interval=50, timeout=self._fit_fonts)
        self._connect()

    # ------------------------------------------------------------ связи с основным окном
    def _connect(self):
        m = self.main
        self._sink = m.player.video.videoSink()
        self._sink.videoFrameChanged.connect(self.view.set_frame)
        m.player.positionChanged.connect(self.rythmo.set_time)
        m.player.playingChanged.connect(self.rythmo.set_playing)
        m.record.cueState.connect(self.view.set_cue)
        m.record.lineChanged.connect(self.show_line)
        self.rythmo.set_project(m.project)
        self.rythmo.set_time(m.player.position())
        self.show_line(m.record.line, m.record.next_text)

    def _disconnect(self):
        m = self.main
        for sig, slot in ((self._sink.videoFrameChanged if self._sink else None, self.view.set_frame),
                          (m.player.positionChanged, self.rythmo.set_time),
                          (m.player.playingChanged, self.rythmo.set_playing),
                          (m.record.cueState, self.view.set_cue),
                          (m.record.lineChanged, self.show_line)):
            if sig is None:
                continue
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def set_project(self, p) -> None:
        self.rythmo.set_project(p)
        self.show_line(None, "")

    def show_line(self, ln, next_text: str = "") -> None:
        p = self.main.project
        if ln is None or p is None:
            self.who.setText("")
            self.prompt.setText("")
            self.next_lbl.setText("")
            return
        ch = p.character(ln.character_id)
        self.who.setText(ch.name if ch else "без роли")
        self.who.setStyleSheet(f"color:{theme.role_text(ch.color)}" if ch else "")
        self.prompt.setText(prompt_html(ln.text))
        self.next_lbl.setText(f"Дальше: {next_text}" if next_text else "")
        self._font_timer.start()

    # ------------------------------------------------------------ вид
    def _fit_fonts(self):
        """Суфлёр крупнее на большом экране."""
        h = max(400, self.height())
        pt = max(18.0, min(44.0, h / 22))
        # размер через стиль: общий стиль темы для cueText иначе перебивает setFont
        self.prompt.setStyleSheet(f"font-size:{pt:.0f}pt")
        self.next_lbl.setStyleSheet(f"font-size:{max(11.0, pt * 0.45):.0f}pt")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._font_timer.start()

    def place(self) -> None:
        """На второй монитор, если он есть, иначе — обычным окном."""
        screens = QGuiApplication.screens()
        main_screen = self.main.screen()
        other = [s for s in screens if s is not main_screen]
        if other:
            g = other[0].availableGeometry()
            self.setGeometry(g)
            self.show()
            self.showFullScreen()
        else:
            self.resize(960, 700)
            self.show()

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _esc(self):
        if self.isFullScreen():
            self.showNormal()

    def closeEvent(self, e):
        self._disconnect()
        self.closed.emit()
        super().closeEvent(e)
