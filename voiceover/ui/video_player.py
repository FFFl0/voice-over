"""Видеоплеер: видео + переключение звука «оригинал / превью озвучки»."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.subtitles import fmt_clock
from . import theme


class VideoPlayer(QWidget):
    positionChanged = Signal(float)   # секунды
    durationChanged = Signal(float)
    playingChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.video.setMinimumSize(320, 180)
        self.video.setObjectName("videoArea")
        self.player.setVideoOutput(self.video)

        # Второй плеер для превью озвучки (видео при этом без звука)
        self.dub = QMediaPlayer(self)
        self.dub_audio = QAudioOutput(self)
        self.dub.setAudioOutput(self.dub_audio)
        self._dub_offset = 0.0     # с какой секунды фильма начинается файл превью
        self._dub_len = 0.0
        self._stop_at: float | None = None

        self.btn_play = QToolButton()
        self.btn_play.setToolTip("Воспроизведение / пауза (Пробел)")
        self.btn_back = QToolButton()
        self.btn_back.setToolTip("Назад 5 с")
        self.btn_fwd = QToolButton()
        self.btn_fwd.setToolTip("Вперёд 5 с")
        self.vol_icon = QLabel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        # Тайм-код — фигура в широком светлом срезе, длительность рядом обычной шириной
        self.time_lbl = QLabel("00:00.000")
        self.time_lbl.setObjectName("timecode")
        self.time_lbl.setFont(theme.display_font(theme.TEXT_XL, tabular=True))
        self.time_lbl.setMinimumWidth(150)
        self.dur_lbl = QLabel("из 00:00.000")
        self.dur_lbl.setObjectName("muted")
        self.dur_lbl.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        self.sound = QComboBox()
        self.sound.addItems(["Звук: оригинал", "Звук: озвучка (превью)", "Без звука"])
        self.sound.setToolTip("Что слышно при воспроизведении. Превью собирается кнопкой "
                              "«Собрать превью» на панели инструментов.")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(90)

        transport = QWidget()
        transport.setObjectName("transport")
        ctl = QHBoxLayout(transport)
        ctl.setContentsMargins(12, 8, 12, 8)
        ctl.setSpacing(6)
        ctl.addWidget(self.time_lbl)
        ctl.addWidget(self.dur_lbl)
        ctl.addSpacing(12)
        for w in (self.btn_back, self.btn_play, self.btn_fwd):
            ctl.addWidget(w)
        ctl.addWidget(self.slider, 1)
        ctl.addSpacing(8)
        ctl.addWidget(self.sound)
        ctl.addWidget(self.vol_icon)
        ctl.addWidget(self.vol)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        lay.addWidget(transport)

        self.btn_play.clicked.connect(self.toggle)
        self.btn_back.clicked.connect(lambda: self.seek(self.position() - 5))
        self.btn_fwd.clicked.connect(lambda: self.seek(self.position() + 5))
        self.slider.sliderMoved.connect(lambda v: self.seek(v / 1000))
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self.sound.currentIndexChanged.connect(self._apply_sound)
        self.vol.valueChanged.connect(self._apply_sound)
        self._apply_sound()

        self._sync = QTimer(self, interval=500, timeout=self._sync_dub)
        self.apply_icons()

    def apply_icons(self) -> None:
        self.btn_back.setIcon(theme.icon("back"))
        self.btn_fwd.setIcon(theme.icon("fwd"))
        self.btn_play.setIcon(theme.icon("pause" if self.is_playing() else "play"))
        self.btn_play.setIconSize(QSize(24, 24))
        self.time_lbl.setFont(theme.display_font(theme.TEXT_XL, tabular=True))
        self.vol_icon.setPixmap(theme.icon("volume", theme.current().muted).pixmap(18, 18))

    # ------------------------------------------------------------ API
    def load(self, path: str) -> None:
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    def clear(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.dub.setSource(QUrl())

    def set_preview(self, wav: str, offset: float, length: float) -> None:
        self.dub.stop()
        self.dub.setSource(QUrl())
        self.dub.setSource(QUrl.fromLocalFile(wav))
        self._dub_offset = offset
        self._dub_len = length
        self.sound.setCurrentIndex(1)

    def position(self) -> float:
        return self.player.position() / 1000.0

    def duration(self) -> float:
        return self.player.duration() / 1000.0

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def seek(self, sec: float) -> None:
        sec = max(0.0, sec)
        self.player.setPosition(int(sec * 1000))
        self._sync_dub(force=True)
        self.positionChanged.emit(sec)

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.pause()
        self._stop_at = None

    def toggle(self) -> None:
        self.pause() if self.is_playing() else self.play()

    def play_range(self, start: float, end: float) -> None:
        """Проиграть фрагмент и остановиться."""
        self.seek(start)
        self._stop_at = end
        self.play()

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)
        self.dub_audio.setMuted(muted)

    # ------------------------------------------------------------ внутреннее
    def _apply_sound(self, *_):
        v = self.vol.value() / 100
        mode = self.sound.currentIndex()
        self.audio.setVolume(v if mode == 0 else 0.0)
        self.dub_audio.setVolume(v if mode == 1 else 0.0)
        self._sync_dub(force=True)

    def _on_pos(self, ms: int):
        sec = ms / 1000
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time_lbl.setText(fmt_clock(sec))
        self.dur_lbl.setText(f"из {fmt_clock(self.duration())}")
        if self._stop_at is not None and sec >= self._stop_at:
            self._stop_at = None
            self.player.pause()
        self.positionChanged.emit(sec)

    def _on_dur(self, ms: int):
        self.slider.setRange(0, ms)
        self.durationChanged.emit(ms / 1000)

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.btn_play.setIcon(theme.icon("pause" if playing else "play"))
        if playing:
            self._sync.start()
        else:
            self._sync.stop()
        self._sync_dub(force=True)
        self.playingChanged.emit(playing)

    def _sync_dub(self, force: bool = False):
        """Держим плеер превью синхронно с видео."""
        if self.dub.source().isEmpty() or self.sound.currentIndex() != 1:
            if self.dub.playbackState() == QMediaPlayer.PlayingState:
                self.dub.pause()
            return
        rel = self.position() - self._dub_offset
        inside = 0 <= rel < self._dub_len
        if not self.is_playing() or not inside:
            self.dub.pause()
            if inside:
                self.dub.setPosition(int(rel * 1000))
            return
        drift = abs(self.dub.position() / 1000 - rel)
        if force or drift > 0.08 or self.dub.playbackState() != QMediaPlayer.PlayingState:
            self.dub.setPosition(int(rel * 1000))
            self.dub.play()
