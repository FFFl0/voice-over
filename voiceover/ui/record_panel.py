"""Панель записи живых актёров: суфлёр, преролл, индикатор уровня, дубли."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import soundfile as sf
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ..core.mixer import silence_bounds
from ..core.models import Line, Project
from ..core.recorder import Recorder, find_device, input_devices, sd_available
from ..core.settings import AppSettings
from . import theme
from .video_player import VideoPlayer


class RecordPanel(QWidget):
    recorded = Signal(str)            # id реплики
    requestLine = Signal(int)         # перейти к соседней реплике: -1 / +1
    playTake = Signal(str)            # путь к файлу дубля

    def __init__(self, player: VideoPlayer, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.player = player
        self.settings = settings
        self.project: Optional[Project] = None
        self.line: Optional[Line] = None
        self.rec: Optional[Recorder] = None
        self._rec_path = ""
        self._rec_t0 = 0.0
        self._get_neighbors: Optional[Callable[[], tuple[str, str]]] = None

        self.device = QComboBox()
        self.btn_refresh = QPushButton(theme.icon("refresh"), "")
        self.btn_refresh.setFixedWidth(40)
        self.btn_refresh.setToolTip("Обновить список устройств")
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(8)
        self.level.setObjectName("level")
        self.preroll = QDoubleSpinBox()
        self.preroll.setRange(0, 10)
        self.preroll.setSuffix(" с")
        self.preroll.setValue(settings.record_preroll)
        self.preroll.setToolTip("Сколько видео проиграть перед началом реплики")
        self.auto_stop = QCheckBox("Автостоп после реплики")
        self.auto_stop.setChecked(True)
        self.auto_next = QCheckBox("Затем к следующей реплике")
        self.auto_next.setChecked(True)
        self.mute_video = QCheckBox("Без звука видео при записи")
        self.mute_video.setToolTip("Включите, если пишете без наушников, чтобы оригинал не попал в микрофон")

        self.prev_lbl = QLabel()
        self.prev_lbl.setObjectName("muted")
        self.prev_lbl.setWordWrap(True)
        self.who = QLabel()
        self.prompt = QLabel("Выберите реплику в таблице")
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 1.6)
        self.prompt.setFont(f)
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(70)
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setObjectName("promptText")
        self.prompt.setStyleSheet("padding:14px; font-size:18pt; font-weight:500")
        self.next_lbl = QLabel()
        self.next_lbl.setObjectName("muted")
        self.next_lbl.setWordWrap(True)
        self.countdown = QLabel()
        self.countdown.setAlignment(Qt.AlignCenter)
        cf = QFont(self.font())
        cf.setPointSizeF(cf.pointSizeF() * 1.4)
        cf.setBold(True)
        self.countdown.setFont(cf)
        self.countdown.setStyleSheet("font-size:14pt; font-weight:700")

        self.btn_rec = QPushButton(theme.icon("record", theme.current().danger), " Записать (R)")
        self.btn_rec.setProperty("danger", True)
        self.btn_stop = QPushButton(theme.icon("stop"), " Стоп")
        self.btn_play = QPushButton(theme.icon("headphones"), " Дубль")
        self.btn_play.setToolTip("Прослушать запись вместе с видео")
        self.btn_del = QPushButton(theme.icon("trash"), " Удалить запись")
        self.btn_prev = QPushButton(theme.icon("back"), "")
        self.btn_prev.setToolTip("Предыдущая реплика этой роли")
        self.btn_next = QPushButton(theme.icon("fwd"), "")
        self.btn_next.setToolTip("Следующая реплика этой роли")
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-10, 10)
        self.offset.setSingleStep(0.05)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" с")
        self.offset.setToolTip("Сдвиг записи относительно начала реплики (подгонка синхрона)")
        self.take_lbl = QLabel()

        form = QFormLayout()
        dev_row = QHBoxLayout()
        dev_row.addWidget(self.device, 1)
        dev_row.addWidget(self.btn_refresh)
        form.addRow("Микрофон:", dev_row)
        form.addRow("Уровень:", self.level)
        form.addRow("Преролл:", self.preroll)
        opts = QHBoxLayout()
        opts.addWidget(self.auto_stop)
        opts.addWidget(self.auto_next)
        form.addRow(opts)
        form.addRow(self.mute_video)

        btns = QHBoxLayout()
        for b in (self.btn_prev, self.btn_rec, self.btn_stop, self.btn_play, self.btn_next):
            btns.addWidget(b)
        take = QHBoxLayout()
        take.addWidget(QLabel("Сдвиг:"))
        take.addWidget(self.offset)
        take.addWidget(self.take_lbl, 1)
        take.addWidget(self.btn_del)

        card = QFrame()
        card.setObjectName("prompt")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.addWidget(self.who)
        cl.addWidget(self.prev_lbl)
        cl.addWidget(self.prompt)
        cl.addWidget(self.next_lbl)
        cl.addWidget(self.countdown)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addLayout(form)
        lay.addWidget(card)
        lay.addLayout(btns)
        lay.addLayout(take)
        lay.addStretch(1)

        self.btn_refresh.clicked.connect(self.refresh_devices)
        self.device.currentIndexChanged.connect(self._device_changed)
        self.btn_rec.clicked.connect(self.start_recording)
        self.btn_stop.clicked.connect(self.stop_recording)
        self.btn_play.clicked.connect(self._play_take)
        self.btn_del.clicked.connect(self._delete_take)
        self.btn_prev.clicked.connect(lambda: self.requestLine.emit(-1))
        self.btn_next.clicked.connect(lambda: self.requestLine.emit(1))
        self.offset.valueChanged.connect(self._offset_changed)
        self.preroll.valueChanged.connect(lambda v: setattr(self.settings, "record_preroll", v))

        self.timer = QTimer(self, interval=50, timeout=self._tick)
        ok, why = sd_available()
        if not ok:
            self.device.addItem(why)
            self.setEnabled(False)
        else:
            self.refresh_devices()
        self._update_buttons()

    # ---------------------------------------------------------- устройства
    def refresh_devices(self):
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Системное по умолчанию", None)
        for idx, name in input_devices():
            self.device.addItem(name, idx)
        i = self.device.findText(self.settings.input_device)
        self.device.setCurrentIndex(max(0, i))
        self.device.blockSignals(False)

    def _device_changed(self, *_):
        self.settings.input_device = self.device.currentText() if self.device.currentIndex() > 0 else ""
        if self.rec and not self.rec.recording:
            self.rec.close()
            self.rec = None

    def _recorder(self) -> Recorder:
        if self.rec is None:
            dev = find_device(self.settings.input_device) if self.settings.input_device else None
            sr = self.project.mix.sample_rate if self.project else 48000
            self.rec = Recorder(dev, sr)
        return self.rec

    def showEvent(self, e):
        super().showEvent(e)
        # индикатор уровня работает, пока панель видна
        try:
            if self.isEnabled():
                self._recorder().start_monitor()
                self.timer.start()
        except Exception as ex:  # noqa: BLE001
            self.level.setToolTip(f"Микрофон недоступен: {ex}")

    def hideEvent(self, e):
        super().hideEvent(e)
        if self.rec and not self.rec.recording:
            self.rec.close()
            self.rec = None
            self.timer.stop()

    # ---------------------------------------------------------- реплика
    def set_project(self, p: Optional[Project]):
        self.project = p
        self.set_line(None)

    def set_line(self, ln: Optional[Line], prev_text: str = "", next_text: str = "") -> None:
        if self.rec and self.rec.recording:
            return
        self.line = ln
        if ln is None or not self.project:
            self.prompt.setText("Выберите реплику в таблице")
            self.who.setText("")
            self.prev_lbl.setText("")
            self.next_lbl.setText("")
        else:
            ch = self.project.character(ln.character_id)
            who = ch.name if ch else "без роли"
            if ch and ch.actor:
                who += f" — {ch.actor}"
            muted = theme.current().muted
            self.who.setText(f"<span style='color:{ch.color if ch else muted};font-size:13pt'>●</span> "
                             f"<b>{who}</b> &nbsp; <span style='color:{muted}'>"
                             f"{ln.start:.2f}–{ln.end:.2f} с · {ln.duration:.1f} с</span>"
                             + (f"<br><i style='color:{muted}'>{ln.note}</i>" if ln.note else ""))
            self.prompt.setText(ln.text)
            self.prev_lbl.setText(f"↑ {prev_text}" if prev_text else "")
            self.next_lbl.setText(f"↓ {next_text}" if next_text else "")
            self.offset.blockSignals(True)
            self.offset.setValue(ln.rec_offset)
            self.offset.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        has_line = self.line is not None
        recording = bool(self.rec and self.rec.recording)
        has_take = bool(has_line and self.line.rec_path and self.project
                        and self.project.resolve(self.line.rec_path).exists())
        self.btn_rec.setEnabled(has_line and not recording)
        self.btn_stop.setEnabled(recording)
        self.btn_play.setEnabled(has_take and not recording)
        self.btn_del.setEnabled(has_take and not recording)
        self.offset.setEnabled(has_take and not recording)
        self.btn_prev.setEnabled(not recording)
        self.btn_next.setEnabled(not recording)
        if has_take:
            p = self.project.resolve(self.line.rec_path)
            try:
                self.take_lbl.setText(f"Дубль: {sf.info(str(p)).duration:.1f} с")
            except Exception:
                self.take_lbl.setText("Дубль есть")
        else:
            self.take_lbl.setText("Записи нет")

    # ---------------------------------------------------------- запись
    def start_recording(self):
        if not self.line or not self.project:
            return
        if not self.project.path:
            QMessageBox.information(self, "Запись", "Сначала сохраните проект — записи "
                                                    "хранятся в папке проекта.")
            return
        rec_dir = self.project.project_dir / "rec"
        rec_dir.mkdir(parents=True, exist_ok=True)
        n = 1
        while (rec_dir / f"{self.line.id}_{n}.wav").exists():
            n += 1
        self._rec_path = str(rec_dir / f"{self.line.id}_{n}.wav")
        pre = self.preroll.value()
        start = max(0.0, self.line.start - pre)
        self._rec_start = start
        try:
            self._recorder().start(self._rec_path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Запись", f"Не удалось начать запись:\n{e}")
            return
        self.player.set_muted(self.mute_video.isChecked())
        self.player.seek(start)
        self.player.play()
        self._rec_t0 = time.monotonic()
        self.timer.start()
        self._update_buttons()

    def stop_recording(self):
        if not self.rec or not self.rec.recording:
            return
        self.rec.stop()
        self.player.pause()
        self.player.set_muted(False)
        self.countdown.setText("")
        ln = self.line
        if ln is None or not self.project:
            return
        # Позицию видео в момент старта записи считаем началом файла; обрезаем тишину
        # по краям и пересчитываем смещение, чтобы синхрон сохранился.
        offset = self._rec_start - ln.start
        try:
            data, sr = sf.read(self._rec_path, dtype="float32")
            mono = data if data.ndim == 1 else data.mean(axis=1)
            a, b = silence_bounds(mono, sr, threshold_db=-40, pad=0.08)
            if b > a:
                offset += a / sr
                sf.write(self._rec_path, data[a:b], sr, subtype="PCM_24")
        except Exception:
            pass
        # Прежние дубли не удаляем: к ним можно вернуться через «Отменить»
        ln.rec_path = self.project.relative(Path(self._rec_path))
        ln.rec_offset = round(offset, 3)
        self.set_line(ln)
        self.recorded.emit(ln.id)
        if self.auto_next.isChecked() and self.auto_stop.isChecked():
            self.requestLine.emit(1)

    def _tick(self):
        if self.rec:
            self.level.setValue(int(min(1.0, self.rec.level * 1.2) * 100))
        if not (self.rec and self.rec.recording and self.line):
            return
        t = self.player.position()
        to_start = self.line.start - t
        if to_start > 0.05:
            self.countdown.setText(f"Приготовьтесь… {to_start:.1f}")
            self.countdown.setStyleSheet(f"color:{theme.current().warn}; font-size:14pt; font-weight:700")
        elif t <= self.line.end:
            self.countdown.setText("● ГОВОРИТЕ")
            self.countdown.setStyleSheet(f"color:{theme.current().danger}; font-size:14pt; font-weight:700")
        else:
            self.countdown.setText("● запись…")
        if self.auto_stop.isChecked() and t > self.line.end + self.settings.record_postroll:
            self.stop_recording()
        # страховка, если видео не загружено
        if time.monotonic() - self._rec_t0 > max(60.0, self.line.duration + 30):
            self.stop_recording()

    def _play_take(self):
        if self.line and self.line.rec_path:
            self.playTake.emit(self.line.id)

    def _delete_take(self):
        if not self.line or not self.project:
            return
        # файл остаётся на диске, чтобы удаление можно было отменить
        self.line.rec_path = ""
        self.line.rec_offset = 0.0
        self.set_line(self.line)
        self.recorded.emit(self.line.id)

    def _offset_changed(self, v: float):
        if self.line:
            self.line.rec_offset = v
            self.recorded.emit(self.line.id)

    def is_recording(self) -> bool:
        return bool(self.rec and self.rec.recording)

    def shutdown(self):
        if self.rec:
            self.rec.close()
            self.rec = None

