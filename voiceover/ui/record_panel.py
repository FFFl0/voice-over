"""Панель записи живых актёров: суфлёр, преролл, индикатор уровня, дубли."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import soundfile as sf
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ..core.mixer import silence_bounds
from ..core.models import Line, Project
from ..core.recorder import Recorder, find_device, input_devices, sd_available
from ..core.settings import AppSettings
from ..core.takes import delete_take, list_takes, remember_offset, reset_edits, select_take
from . import theme
from .video_player import VideoPlayer


def prompt_html(text: str) -> str:
    """Текст для суфлёра: пометки в скобках — приглушённым курсивом (их не произносят)."""
    import html
    import re
    muted = theme.current().muted
    out, pos = [], 0
    for m in re.finditer(r"\([^)]*\)|\[[^\]]*\]", text):
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<span style="color:{muted}; font-style:italic">{html.escape(m.group())}</span>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


class RecordPanel(QWidget):
    recorded = Signal(str)            # id реплики
    requestLine = Signal(int)         # перейти к соседней реплике: -1 / +1
    playTake = Signal(str)            # путь к файлу дубля
    alignRequested = Signal(str)      # id реплики: подогнать запись к оригиналу
    cueState = Signal(str, float, float)   # режим отсчёта, секунд до реплики, прошло (cue.py)

    def __init__(self, player: VideoPlayer, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.player = player
        self.settings = settings
        self.project: Optional[Project] = None
        self.line: Optional[Line] = None
        self.rec: Optional[Recorder] = None
        self._rec_path = ""
        self._rec_t0 = 0.0
        self.loop_pending = False   # пауза между дублями при записи по кругу
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
        self.loop = QCheckBox("По кругу: дубль за дублем")
        self.loop.setToolTip("После каждого дубля запись той же реплики начинается снова. "
                             "Остановите кнопкой «Стоп» и выберите лучший дубль в списке.")
        self.mute_video = QCheckBox("Без звука видео при записи")
        self.mute_video.setToolTip("Включите, если пишете без наушников, чтобы оригинал не попал в микрофон")

        self.prev_lbl = QLabel()
        self.prev_lbl.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.prev_lbl.setObjectName("muted")
        self.prev_lbl.setWordWrap(True)
        self.who = QLabel()
        self.who.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.who.setObjectName("itemTitle")
        self.when = QLabel()
        self.when.setObjectName("muted")
        self.when.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        self.length = QLabel()
        self.length.setObjectName("muted")
        self.length.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        self.note = QLabel()
        self.note.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.prompt = QLabel("Выберите реплику в таблице")
        self.prompt.setTextFormat(Qt.RichText)   # текст экранируется в prompt_html()
        self.prompt.setObjectName("promptText")
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(110)
        self.prompt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.next_lbl = QLabel()
        self.next_lbl.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.next_lbl.setObjectName("muted")
        self.next_lbl.setWordWrap(True)
        # Отсчёт — метр (2 px): до начала реплики заполняется преролл, затем сама реплика
        self.countdown = QLabel()
        self.countdown.setObjectName("countdown")
        self.meter = QProgressBar()
        self.meter.setRange(0, 1000)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)

        self.btn_rec = QPushButton(theme.icon("record", theme.current().danger), " Записать (R)")
        self.btn_rec.setProperty("danger", True)
        self.btn_stop = QPushButton(theme.icon("stop"), " Стоп")
        self.btn_play = QPushButton(theme.icon("headphones"), " Дубль")
        self.btn_play.setToolTip("Прослушать запись вместе с видео")
        self.btn_del = QPushButton(theme.icon("trash"), "")
        self.btn_del.setToolTip("Удалить выбранный дубль с диска")
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
        self.btn_align = QPushButton(theme.icon("refresh"), " Подогнать")
        self.btn_align.setToolTip("Подогнать сдвиг записи к голосу оригинала "
                                  "(по слогам и паузам)")
        self.auto_align = QCheckBox("Подгонять к оригиналу после записи")
        self.auto_align.setChecked(settings.record_auto_align)
        self.auto_align.toggled.connect(lambda v: setattr(self.settings, "record_auto_align", v))
        def spin(lo, hi, step, suffix, tip, dec=2):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setDecimals(dec)
            s.setSuffix(suffix)
            s.setToolTip(tip)
            s.valueChanged.connect(self._edits_changed)
            return s
        self.gain = spin(-24, 24, 0.5, " дБ", "Громкость этой реплики", 1)
        self.trim_in = spin(0, 30, 0.05, " с", "Обрезать начало дубля")
        self.trim_out = spin(0, 30, 0.05, " с", "Обрезать конец дубля")
        self.fade_in = spin(0, 5, 0.05, " с", "Плавное нарастание в начале")
        self.fade_out = spin(0, 5, 0.05, " с", "Плавное затухание в конце")
        self.take_box = QComboBox()
        self.take_box.setToolTip("Дубли реплики. Выбранный идёт в сведение; остальные хранятся, "
                                 "к ним можно вернуться. Слушать — P.")
        self.take_box.setMinimumWidth(200)

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
        opts.addWidget(self.loop)
        form.addRow(opts)
        form.addRow(self.mute_video)
        form.addRow(self.auto_align)

        btns = QHBoxLayout()
        for b in (self.btn_prev, self.btn_rec, self.btn_stop, self.btn_play, self.btn_next):
            btns.addWidget(b)
        take = QHBoxLayout()
        take.addWidget(QLabel("Дубль:"))
        take.addWidget(self.take_box, 1)
        take.addWidget(self.btn_del)
        take.addSpacing(12)
        take.addWidget(QLabel("Сдвиг:"))
        take.addWidget(self.offset)
        take.addWidget(self.btn_align)

        edits = QHBoxLayout()
        for lbl, w in (("Громкость:", self.gain), ("Обрезать:", self.trim_in), ("–", self.trim_out),
                       ("Нарастание:", self.fade_in), ("Затухание:", self.fade_out)):
            edits.addWidget(QLabel(lbl))
            edits.addWidget(w)
        edits.addStretch(1)

        card = QFrame()
        card.setObjectName("well")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self.who)
        head.addStretch(1)
        head.addWidget(self.when)
        head.addSpacing(16)
        head.addWidget(self.length)
        cl.addLayout(head)
        cl.addWidget(self.note)
        cl.addSpacing(6)
        cl.addWidget(self.prev_lbl)
        cl.addWidget(self.prompt)
        cl.addWidget(self.next_lbl)
        cl.addSpacing(8)
        cl.addWidget(self.meter)
        cl.addWidget(self.countdown)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addLayout(form)
        lay.addWidget(card)
        lay.addLayout(btns)
        lay.addLayout(take)
        lay.addLayout(edits)
        from .take_editor import TakeEditor
        self.editor = TakeEditor()
        lay.addWidget(self.editor, 1)
        self.editor.changed.connect(self._editor_changed)
        self.editor.edited.connect(lambda _l: self.line and self.recorded.emit(self.line.id))
        self.editor.seekRequested.connect(self.player.seek)
        self.player.positionChanged.connect(
            lambda t: self.editor.isVisible() and self.editor.set_playhead(t))

        self.btn_refresh.clicked.connect(self.refresh_devices)
        self.device.currentIndexChanged.connect(self._device_changed)
        self.btn_rec.clicked.connect(self.start_recording)
        self.btn_stop.clicked.connect(lambda: self.stop_recording(manual=True))
        self.btn_play.clicked.connect(self._play_take)
        self.btn_del.clicked.connect(self._delete_take)
        self.btn_prev.clicked.connect(lambda: self.requestLine.emit(-1))
        self.btn_next.clicked.connect(lambda: self.requestLine.emit(1))
        self.offset.valueChanged.connect(self._offset_changed)
        self.take_box.activated.connect(self._take_chosen)
        self.btn_align.clicked.connect(lambda: self.line and self.alignRequested.emit(self.line.id))
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
            for w in (self.who, self.when, self.length, self.note, self.prev_lbl, self.next_lbl):
                w.setText("")
        else:
            ch = self.project.character(ln.character_id)
            who = ch.name if ch else "без роли"
            if ch and ch.actor:
                who += f" — {ch.actor}"
            self.who.setText(who)
            # цвет роли — данные проекта, а не цвет интерфейса
            self.who.setStyleSheet(
                f"color:{theme.role_text(ch.color, theme.current().paper3)}" if ch else "")
            self.when.setText(f"{ln.start:.2f}–{ln.end:.2f} с")
            self.length.setText(f"{ln.duration:.1f} с")
            self.note.setText(ln.note)
            self.note.setVisible(bool(ln.note))
            self.prompt.setText(prompt_html(ln.text))
            self.prev_lbl.setText(f"Перед этим: {prev_text}" if prev_text else "")
            self.next_lbl.setText(f"Дальше: {next_text}" if next_text else "")
            self.meter.setValue(0)
            self.countdown.setText("")
            self.offset.blockSignals(True)
            self.offset.setValue(ln.rec_offset)
            self.offset.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        has_line = self.line is not None
        recording = bool(self.rec and self.rec.recording)
        has_take = bool(has_line and self.line.rec_path and self.project
                        and self.project.resolve(self.line.rec_path).exists())
        self._fill_takes()
        self._load_edits()
        self.btn_rec.setEnabled(has_line and not recording)
        self.btn_stop.setEnabled(recording)
        self.btn_play.setEnabled(has_take and not recording)
        self.btn_del.setEnabled(has_take and not recording)
        self.take_box.setEnabled(self.take_box.count() > 1 and not recording)
        self.offset.setEnabled(has_take and not recording)
        self.btn_align.setEnabled(has_take and not recording)
        for w in (self.gain, self.trim_in, self.trim_out, self.fade_in, self.fade_out):
            w.setEnabled(has_take and not recording)
        self.btn_prev.setEnabled(not recording)
        self.btn_next.setEnabled(not recording)

    def _load_edits(self):
        ln = self.line
        ch = self.project.character(ln.character_id) if (ln and self.project) else None
        self.editor.set_line(self.project, ln, ch.color if ch else "")
        for w, v in ((self.gain, ln.gain_db if ln else 0.0), (self.trim_in, ln.trim_in if ln else 0.0),
                     (self.trim_out, ln.trim_out if ln else 0.0),
                     (self.fade_in, ln.fade_in if ln else 0.0),
                     (self.fade_out, ln.fade_out if ln else 0.0)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)

    def _editor_changed(self):
        """Правка мышью в редакторе дубля — обновить поля, не вызывая их обработчики."""
        ln = self.line
        if not ln:
            return
        for w, v in ((self.trim_in, ln.trim_in), (self.trim_out, ln.trim_out),
                     (self.fade_in, ln.fade_in), (self.fade_out, ln.fade_out),
                     (self.offset, ln.rec_offset)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        remember_offset(ln)

    def _edits_changed(self, *_):
        ln = self.line
        if not ln:
            return
        ln.gain_db = self.gain.value()
        ln.trim_in, ln.trim_out = self.trim_in.value(), self.trim_out.value()
        ln.fade_in, ln.fade_out = self.fade_in.value(), self.fade_out.value()
        self.editor.update()
        self.recorded.emit(ln.id)

    def refresh_take(self) -> None:
        """Обновить сдвиг и список дублей (после изменения реплики снаружи)."""
        if self.line is None or self.is_recording():
            return
        self.offset.blockSignals(True)
        self.offset.setValue(self.line.rec_offset)
        self.offset.blockSignals(False)
        self._update_buttons()

    def _fill_takes(self):
        import datetime
        self.take_box.blockSignals(True)
        self.take_box.clear()
        self.take_box.addItem("— без записи —", "")
        if self.line is not None and self.project is not None:
            for tk in list_takes(self.project, self.line):
                when = datetime.datetime.fromtimestamp(tk.mtime).strftime("%H:%M")
                self.take_box.addItem(f"Дубль {tk.number} · {tk.duration:.1f} с · {when}", tk.rel)
            i = self.take_box.findData(self.line.rec_path or "")
            self.take_box.setCurrentIndex(max(0, i))
        self.take_box.blockSignals(False)

    def _take_chosen(self, i: int):
        if not self.line:
            return
        rel = self.take_box.itemData(i) or ""
        if rel == self.line.rec_path:
            return
        select_take(self.line, rel)
        self.set_line(self.line)
        self.recorded.emit(self.line.id)

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

    def stop_recording(self, manual: bool = False):
        if manual and self.loop_pending:   # «Стоп» в паузе между дублями — круг окончен
            self.loop_pending = False
            self.countdown.setText("")
            self.cueState.emit("", 0.0, 0.0)
            return
        if not self.rec or not self.rec.recording:
            return
        self.rec.stop()
        self.player.pause()
        self.player.set_muted(False)
        self.countdown.setText("")
        self.cueState.emit("", 0.0, 0.0)
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
        # Прежние дубли не удаляем: они остаются в списке «Дубль»
        remember_offset(ln)
        reset_edits(ln)
        ln.rec_path = self.project.relative(Path(self._rec_path))
        ln.rec_offset = round(offset, 3)
        remember_offset(ln)
        self.set_line(ln)
        self.recorded.emit(ln.id)
        if self.auto_align.isChecked():
            self.alignRequested.emit(ln.id)
        if self.loop.isChecked() and not manual:
            self.countdown.setText("Следующий дубль…")
            self.cueState.emit("next", 0.0, 0.0)
            self.loop_pending = True
            QTimer.singleShot(900, self._loop_again)
        elif self.auto_next.isChecked() and self.auto_stop.isChecked():
            self.requestLine.emit(1)

    def _loop_again(self):
        if not self.loop_pending:
            return
        self.loop_pending = False
        if self.loop.isChecked() and self.line is not None and not self.is_recording() \
                and self.isVisible():
            self.start_recording()

    def _tick(self):
        if self.rec:
            self.level.setValue(int(min(1.0, self.rec.level * 1.2) * 100))
        if not (self.rec and self.rec.recording and self.line):
            return
        t = self.player.position()
        to_start = self.line.start - t
        th = theme.current()
        if to_start > 0.05:
            pre = max(0.01, self.line.start - self._rec_start)
            self.meter.setValue(int(1000 * (1 - to_start / pre)))
            self.countdown.setText(f"Приготовьтесь, {to_start:.1f} с")
            self.countdown.setStyleSheet(f"color:{th.muted}")
            self.cueState.emit("prep", to_start, time.monotonic() - self._rec_t0)
        elif t <= self.line.end:
            self.meter.setValue(int(1000 * (t - self.line.start) / max(0.01, self.line.duration)))
            self.countdown.setText("Говорите")
            self.countdown.setStyleSheet(f"color:{th.ink}")
            self.cueState.emit("speak", 0.0, max(0.0, t - self.line.start))
        else:
            self.meter.setValue(1000)
            self.countdown.setText("Запись идёт, можно остановить")
            self.countdown.setStyleSheet(f"color:{th.muted}")
            self.cueState.emit("tail", 0.0, max(0.0, t - self.line.start))
        if (self.auto_stop.isChecked() or self.loop.isChecked()) and \
                t > self.line.end + self.settings.record_postroll:
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
        rel = self.line.rec_path
        if not rel or QMessageBox.question(
                self, "Удалить дубль", "Удалить этот дубль с диска? Это нельзя отменить."
        ) != QMessageBox.Yes:
            return
        delete_take(self.project, self.line, rel)
        self.set_line(self.line)
        self.recorded.emit(self.line.id)

    def _offset_changed(self, v: float):
        if self.line:
            self.line.rec_offset = v
            remember_offset(self.line)
            self.editor.set_line(self.project, self.line)
            self.recorded.emit(self.line.id)

    def is_recording(self) -> bool:
        return bool(self.rec and self.rec.recording)

    def shutdown(self):
        if self.rec:
            self.rec.close()
            self.rec = None

