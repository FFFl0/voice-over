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
        self.video.setMinimumSize(240, 120)
        self.video.setObjectName("videoArea")
        self.player.setVideoOutput(self.video)

        # Второй плеер для превью озвучки (видео при этом без звука)
        self.dub = QMediaPlayer(self)
        self.dub_audio = QAudioOutput(self)
        self.dub.setAudioOutput(self.dub_audio)
        self._dub_offset = 0.0     # с какой секунды фильма начинается файл превью
        self._dub_len = 0.0
        self._stop_at: float | None = None
        # Дорожки после отделения голосов (голоса, фон) — играют вместо звука видео
        # в режиме «оригинал», чтобы любую можно было заглушить
        self.stems: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        self.muted_tracks: set[str] = set()

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
        self.time_lbl.setFont(theme.display_font(theme.TEXT_2XL, tabular=True))
        self.time_lbl.setMinimumWidth(120)
        self.dur_lbl = QLabel("из 00:00.000")
        self.dur_lbl.setObjectName("muted")
        self.dur_lbl.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        self.sound = QComboBox()
        self.main_track = 0            # дорожка оригинала проекта (по ней отделены голоса)
        self.set_audio_tracks([])
        self.sound.setToolTip("Что слышно при воспроизведении. Превью собирается кнопкой "
                              "«Собрать превью» на панели инструментов.")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(70)
        # длинные названия дорожек не должны раздвигать окно
        self.sound.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sound.setMinimumContentsLength(12)
        self.slider.setMinimumWidth(60)

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
        self.player.tracksChanged.connect(self._apply_sound)   # дорожки известны после загрузки
        self.vol.valueChanged.connect(self._apply_sound)
        self._apply_sound()

        self._sync = QTimer(self, interval=500, timeout=self._sync_dub)
        self.apply_icons()

    def apply_icons(self) -> None:
        self.btn_back.setIcon(theme.icon("back"))
        self.btn_fwd.setIcon(theme.icon("fwd"))
        self.btn_play.setIcon(theme.icon("pause" if self.is_playing() else "play"))
        self.btn_play.setIconSize(QSize(24, 24))
        self.time_lbl.setFont(theme.display_font(theme.TEXT_2XL, tabular=True))
        self.vol_icon.setPixmap(theme.icon("volume", theme.current().muted).pixmap(18, 18))

    # ------------------------------------------------------------ API
    def load(self, path: str) -> None:
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    def clear(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.dub.setSource(QUrl())
        self.set_stems({})

    def set_stems(self, paths: dict[str, str]) -> None:
        """paths: ключ дорожки → wav. Пустой словарь — играет звук самого видео."""
        for pl, _out in self.stems.values():
            pl.stop()
            pl.setSource(QUrl())
            pl.deleteLater()
        self.stems = {}
        for key, path in paths.items():
            pl = QMediaPlayer(self)
            out = QAudioOutput(self)
            pl.setAudioOutput(out)
            pl.setSource(QUrl.fromLocalFile(path))
            self.stems[key] = (pl, out)
        self._apply_sound()

    def set_muted_tracks(self, keys: set[str]) -> None:
        self.muted_tracks = set(keys)
        self._apply_sound()

    def release_stems(self) -> None:
        """Освободить файлы дорожек (перед повторным отделением голосов)."""
        for pl, _out in self.stems.values():
            pl.stop()
            pl.setSource(QUrl())

    def set_preview(self, wav: str, offset: float, length: float) -> None:
        self.dub.stop()
        self.dub.setSource(QUrl())
        self.dub.setSource(QUrl.fromLocalFile(wav))
        self._dub_offset = offset
        self._dub_len = length
        self.set_sound_mode("dub")

    # ------------------------------------------------------------ дорожки
    def set_audio_tracks(self, labels: list[str], main: int = 0) -> None:
        """Пункты «Звук»: каждая дорожка оригинала, превью озвучки, без звука.
        Данные пункта — строка: «orig:N», «dub», «none»."""
        prev = self.sound.currentData() if self.sound.count() else f"orig:{main}"
        if prev == f"orig:{self.main_track}":
            prev = f"orig:{main}"          # слушали основную — остаёмся на основной
        self.main_track = main
        self.sound.blockSignals(True)
        self.sound.clear()
        if len(labels) <= 1:
            self.sound.addItem("Звук: оригинал", f"orig:{main}")
        else:
            for i, lbl in enumerate(labels):
                mark = " — основная" if i == main else ""
                self.sound.addItem(f"Оригинал {i + 1}: {lbl}{mark}", f"orig:{i}")
        self.sound.addItem("Звук: озвучка (превью)", "dub")
        self.sound.addItem("Без звука", "none")
        i = self.sound.findData(prev)
        if i < 0 and str(prev).startswith("orig"):
            i = self.sound.findData(f"orig:{main}")
        self.sound.setCurrentIndex(max(0, i))
        self.sound.blockSignals(False)
        if hasattr(self, "vol"):
            self._apply_sound()

    def sound_mode(self) -> str:
        d = str(self.sound.currentData() or "orig")
        return d.split(":")[0]

    def orig_track(self) -> int:
        d = str(self.sound.currentData() or "")
        return int(d.split(":")[1]) if d.startswith("orig:") else self.main_track

    def set_sound_mode(self, mode: str) -> None:
        key = f"orig:{self.main_track}" if mode == "orig" else mode
        i = self.sound.findData(key)
        if i >= 0:
            self.sound.setCurrentIndex(i)

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
        for _pl, out in self.stems.values():
            out.setMuted(muted)

    # ------------------------------------------------------------ внутреннее
    def _apply_sound(self, *_):
        v = self.vol.value() / 100
        mode = self.sound_mode()
        track = self.orig_track()
        tracks = self.player.audioTracks()
        if mode == "orig" and len(tracks) > 1 and track < len(tracks) \
                and self.player.activeAudioTrack() != track:
            self.player.setActiveAudioTrack(track)
        # отделённые голоса и фон относятся только к основной дорожке
        use_stems = bool(self.stems) and track == self.main_track
        orig_on = mode == "orig" and not use_stems and not (
            track == self.main_track and "original" in self.muted_tracks)
        self.audio.setVolume(v if orig_on else 0.0)
        self.dub_audio.setVolume(v if mode == "dub" else 0.0)
        for key, (_pl, out) in self.stems.items():
            out.setVolume(v if mode == "orig" and use_stems and key not in self.muted_tracks
                          else 0.0)
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
        """Держим плеер превью (и дорожки голосов/фона) синхронно с видео."""
        self._sync_stems(force)
        if self.dub.source().isEmpty() or self.sound_mode() != "dub":
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

    def _sync_stems(self, force: bool = False) -> None:
        on = self.sound_mode() == "orig" and self.orig_track() == self.main_track \
            and self.is_playing()
        pos = self.position()
        for key, (pl, _out) in self.stems.items():
            if not on or key in self.muted_tracks:
                if pl.playbackState() == QMediaPlayer.PlayingState:
                    pl.pause()
                if not on and not self.is_playing():
                    pl.setPosition(int(pos * 1000))
                continue
            drift = abs(pl.position() / 1000 - pos)
            if force or drift > 0.08 or pl.playbackState() != QMediaPlayer.PlayingState:
                pl.setPosition(int(pos * 1000))
                pl.play()
