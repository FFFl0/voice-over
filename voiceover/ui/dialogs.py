"""Диалоги: новый проект, импорт субтитров, экспорт, настройки, параметры сведения."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core import ffmpeg
from ..core.models import MixMode, MixSettings
from ..core.settings import AppSettings
from ..core.subtitles import SubLine, fmt_clock, load_subtitles

VIDEO_FILTER = "Видео (*.mkv *.mp4 *.avi *.mov *.webm *.m4v *.ts *.m2ts *.wmv *.flv);;Все файлы (*)"
SUB_FILTER = "Субтитры (*.srt *.ass *.ssa *.vtt);;Все файлы (*)"


def path_row(edit: QLineEdit, caption: str, filt: str, save: bool = False,
             parent: Optional[QWidget] = None) -> QHBoxLayout:
    btn = QPushButton("Обзор…")

    def pick():
        if save:
            p, _ = QFileDialog.getSaveFileName(parent, caption, edit.text(), filt)
        else:
            p, _ = QFileDialog.getOpenFileName(parent, caption, edit.text(), filt)
        if p:
            edit.setText(p)

    btn.clicked.connect(pick)
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(btn)
    return row


# ======================================================================== новый проект

class NewProjectDialog(QDialog):
    def __init__(self, parent=None, video: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Новый проект озвучки")
        self.setMinimumWidth(560)
        self.info: Optional[ffmpeg.MediaInfo] = None

        self.video = QLineEdit(video)
        self.stream = QComboBox()
        self.subs = QLineEdit()
        self.subs.setPlaceholderText("необязательно — можно импортировать позже")
        self.mode_vo = QRadioButton("Закадровая озвучка — оригинал слышен тихо под репликами")
        self.mode_dub = QRadioButton("Дубляж — оригинальные голоса удаляются, музыка и звуки остаются")
        self.mode_vo.setChecked(True)
        self.info_lbl = QLabel()
        self.info_lbl.setStyleSheet("color:gray")

        f = QFormLayout()
        f.addRow("Видео:", path_row(self.video, "Выберите видео", VIDEO_FILTER, parent=self))
        f.addRow("", self.info_lbl)
        f.addRow("Аудиодорожка:", self.stream)
        f.addRow("Субтитры:", path_row(self.subs, "Субтитры", SUB_FILTER, parent=self))
        mode = QGroupBox("Тип озвучки")
        ml = QVBoxLayout(mode)
        ml.addWidget(self.mode_vo)
        ml.addWidget(self.mode_dub)
        ml.addWidget(QLabel("<small>Для дубляжа нужно «Отделить голоса» (Demucs). "
                            "Тип можно изменить позже в панели «Сведение».</small>"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(mode)
        lay.addWidget(bb)
        self.video.textChanged.connect(self._probe)
        if video:
            self._probe()

    def _probe(self, *_):
        self.stream.clear()
        self.info = None
        p = self.video.text().strip()
        if not p or not Path(p).is_file():
            self.info_lbl.setText("")
            return
        try:
            self.info = ffmpeg.probe(p)
        except Exception as e:  # noqa: BLE001
            self.info_lbl.setText(f"Ошибка: {e}")
            return
        self.info_lbl.setText(f"Длительность: {fmt_clock(self.info.duration)}, "
                              f"аудиодорожек: {len(self.info.audio)}, "
                              f"субтитров: {len(self.info.subtitles)}")
        for a in self.info.audio:
            label = f"#{a.type_index + 1}: {a.codec}"
            if a.language:
                label += f" [{a.language}]"
            if a.title:
                label += f" «{a.title}»"
            label += f" — {a.description[:40]}"
            self.stream.addItem(label, a.type_index)
        # Если рядом лежат субтитры с тем же именем — подставим
        if not self.subs.text():
            base = Path(p)
            for ext in (".ass", ".srt", ".vtt", ".ssa"):
                cand = base.with_suffix(ext)
                if cand.exists():
                    self.subs.setText(str(cand))
                    break

    def _accept(self):
        if not self.info:
            QMessageBox.warning(self, "Новый проект", "Выберите видеофайл.")
            return
        if not self.info.audio:
            QMessageBox.warning(self, "Новый проект", "В файле нет аудиодорожек.")
            return
        self.accept()

    @property
    def mode(self) -> MixMode:
        return MixMode.DUBBING if self.mode_dub.isChecked() else MixMode.VOICEOVER


# ======================================================================== импорт субтитров

class ImportSubsDialog(QDialog):
    """Предпросмотр импорта: как будут распределены персонажи."""

    def __init__(self, path: str, has_lines: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Импорт субтитров — {Path(path).name}")
        self.resize(760, 520)
        self.path = path
        self.lines: list[SubLine] = []
        ext = Path(path).suffix.lower()

        self.detect = QCheckBox("Определять персонажа по префиксу «Имя: текст» / «[Имя] текст»")
        self.detect.setChecked(True)
        self.ass_field = QComboBox()
        self.ass_field.addItem("Авто (Actor, иначе Style)", "auto")
        self.ass_field.addItem("Поле Actor/Name", "name")
        self.ass_field.addItem("Стиль (Style)", "style")
        self.replace = QCheckBox("Заменить существующие реплики")
        self.replace.setChecked(True)
        self.replace.setVisible(has_lines)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3600, 3600)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" с")
        self.offset.setToolTip("Сдвинуть все тайминги (если субтитры от другого релиза)")
        self.summary = QLabel()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Начало", "Конец", "Персонаж", "Текст"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        f = QFormLayout()
        if ext in (".ass", ".ssa"):
            f.addRow("Персонаж из:", self.ass_field)
        else:
            f.addRow(self.detect)
        f.addRow("Сдвиг времени:", self.offset)
        f.addRow(self.replace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Импортировать")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)
        lay.addWidget(bb)
        self.detect.toggled.connect(self._reload)
        self.ass_field.currentIndexChanged.connect(self._reload)
        self._reload()

    def _reload(self, *_):
        try:
            self.lines = load_subtitles(self.path, self.detect.isChecked(),
                                        self.ass_field.currentData())
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"Ошибка чтения: {e}")
            self.lines = []
        speakers: dict[str, int] = {}
        for s in self.lines:
            if s.speaker:
                speakers[s.speaker] = speakers.get(s.speaker, 0) + 1
        top = ", ".join(f"{k} ({v})" for k, v in sorted(speakers.items(), key=lambda x: -x[1])[:12])
        self.summary.setText(f"Реплик: {len(self.lines)}. Персонажей: {len(speakers)}"
                             + (f" — {top}" if top else
                                ". Роли не найдены — назначьте их в таблице реплик."))
        self.table.setRowCount(min(len(self.lines), 500))
        for i, s in enumerate(self.lines[:500]):
            for j, v in enumerate((fmt_clock(s.start), fmt_clock(s.end), s.speaker, s.text)):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()


# ======================================================================== сведение

class MixPanel(QWidget):
    """Параметры сведения (панель справа)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mix: Optional[MixSettings] = None
        self.on_change = None
        self.mode = QComboBox()
        self.mode.addItem("Закадровая (оригинал приглушается)", MixMode.VOICEOVER.value)
        self.mode.addItem("Дубляж (голоса оригинала удалены)", MixMode.DUBBING.value)
        self.duck = self._spin(-60, 0, " дБ", "Насколько приглушать оригинал под репликами")
        self.attack = self._spin(0.0, 2, " с", "Время плавного приглушения перед репликой", 0.05)
        self.release = self._spin(0.0, 3, " с", "Время возврата громкости после реплики", 0.05)
        self.bg = self._spin(-40, 12, " дБ", "Громкость оригинала / фона")
        self.voices = self._spin(-30, 20, " дБ", "Общая громкость голосов")
        self.normalize = QCheckBox("Выравнивать громкость реплик")
        self.speed = self._spin(1.0, 2.0, "×", "Максимальное ускорение реплики, чтобы она "
                                               "уложилась в тайминг", 0.05)
        self.bg_status = QLabel()
        self.bg_status.setWordWrap(True)
        f = QFormLayout(self)
        f.addRow("Режим:", self.mode)
        f.addRow(self.bg_status)
        f.addRow("Приглушение оригинала:", self.duck)
        f.addRow("Атака:", self.attack)
        f.addRow("Восстановление:", self.release)
        f.addRow("Громкость фона:", self.bg)
        f.addRow("Громкость голосов:", self.voices)
        f.addRow(self.normalize)
        f.addRow("Макс. ускорение:", self.speed)
        self.mode.currentIndexChanged.connect(self._save)
        self.normalize.toggled.connect(self._save)
        self.setEnabled(False)

    def _spin(self, lo, hi, suffix, tip, step=1.0):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setSuffix(suffix)
        s.setToolTip(tip)
        s.valueChanged.connect(self._save)
        return s

    def set_mix(self, mix: Optional[MixSettings], has_background: bool = False) -> None:
        self.mix = None
        self.setEnabled(mix is not None)
        if mix is None:
            return
        self.mode.setCurrentIndex(self.mode.findData(mix.mode.value))
        self.duck.setValue(mix.duck_db)
        self.attack.setValue(mix.duck_attack)
        self.release.setValue(mix.duck_release)
        self.bg.setValue(mix.background_db)
        self.voices.setValue(mix.voices_db)
        self.normalize.setChecked(mix.normalize_voices)
        self.speed.setValue(mix.max_speedup)
        self.mix = mix
        self.set_background_status(has_background)
        self._update()

    def set_background_status(self, ok: bool) -> None:
        self._has_bg = ok
        self._update()

    def _update(self):
        dub = self.mode.currentData() == MixMode.DUBBING.value
        for w in (self.duck, self.attack, self.release):
            w.setEnabled(not dub)
        if dub:
            if getattr(self, "_has_bg", False):
                self.bg_status.setText("✔ Фон без голосов готов.")
                self.bg_status.setStyleSheet("color:#2e7d32")
            else:
                self.bg_status.setText("⚠ Фон без голосов ещё не создан: "
                                       "меню «Обработка → Отделить голоса».")
                self.bg_status.setStyleSheet("color:#ef6c00")
        else:
            self.bg_status.setText("")

    def _save(self, *_):
        if self.mix is None:
            return
        m = self.mix
        m.mode = MixMode(self.mode.currentData())
        m.duck_db = self.duck.value()
        m.duck_attack = self.attack.value()
        m.duck_release = self.release.value()
        m.background_db = self.bg.value()
        m.voices_db = self.voices.value()
        m.normalize_voices = self.normalize.isChecked()
        m.max_speedup = self.speed.value()
        self._update()
        if self.on_change:
            self.on_change()


# ======================================================================== экспорт

class ExportDialog(QDialog):
    def __init__(self, default_out: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Экспорт озвучки")
        self.setMinimumWidth(560)
        self.kind = QComboBox()
        self.kind.addItem("Видео с новой звуковой дорожкой", "video")
        self.kind.addItem("Только звуковая дорожка", "audio")
        self.out = QLineEdit(default_out)
        self.container = QComboBox()
        self.container.addItem("MKV (рекомендуется)", "mkv")
        self.container.addItem("MP4", "mp4")
        self.codec = QComboBox()
        for k, (_, title) in ffmpeg.AUDIO_CODECS.items():
            self.codec.addItem(title, k)
        self.bitrate = QComboBox()
        self.bitrate.addItems(["128k", "192k", "256k", "320k", "384k", "448k", "640k"])
        self.bitrate.setCurrentText("256k")
        self.keep_orig = QCheckBox("Оставить оригинальную дорожку (второй)")
        self.keep_orig.setChecked(True)
        self.keep_subs = QCheckBox("Сохранить субтитры из исходного видео")
        self.keep_subs.setChecked(True)
        self.title = QLineEdit("Многоголосая озвучка")
        self.lang = QLineEdit("rus")
        self.lang.setMaxLength(3)
        self.lang.setFixedWidth(60)

        self.out_btn = QPushButton("Обзор…")
        self.out_btn.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.out, 1)
        row.addWidget(self.out_btn)
        f = QFormLayout()
        f.addRow("Что сохранить:", self.kind)
        f.addRow("Файл:", row)
        f.addRow("Контейнер:", self.container)
        f.addRow("Кодек звука:", self.codec)
        f.addRow("Битрейт:", self.bitrate)
        f.addRow("Название дорожки:", self.title)
        f.addRow("Язык (ISO 639-2):", self.lang)
        f.addRow(self.keep_orig)
        f.addRow(self.keep_subs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Экспортировать")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._update)
        self.container.currentIndexChanged.connect(self._update)
        self.codec.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self, *_):
        video = self.kind.currentData() == "video"
        for w in (self.container, self.keep_orig, self.keep_subs):
            w.setEnabled(video)
        self.bitrate.setEnabled(self.codec.currentData() != "flac")
        p = Path(self.out.text())
        if video:
            ext = "." + self.container.currentData()
        else:
            ext = {"aac": ".m4a", "ac3": ".ac3", "opus": ".mka", "flac": ".flac"}[self.codec.currentData()]
        if p.name:
            self.out.setText(str(p.with_suffix(ext)))

    def _pick(self):
        filt = "Видео (*.mkv *.mp4)" if self.kind.currentData() == "video" else \
            "Аудио (*.m4a *.ac3 *.mka *.flac *.wav)"
        p, _ = QFileDialog.getSaveFileName(self, "Куда сохранить", self.out.text(), filt)
        if p:
            self.out.setText(p)
            self._update()

    def options(self) -> ffmpeg.ExportOptions:
        return ffmpeg.ExportOptions(
            container=self.container.currentData(),
            codec=self.codec.currentData(),
            bitrate=self.bitrate.currentText(),
            keep_original=self.keep_orig.isChecked(),
            keep_subtitles=self.keep_subs.isChecked(),
            track_title=self.title.text().strip() or "Озвучка",
            language=(self.lang.text().strip() or "rus")[:3],
        )


# ======================================================================== настройки

class SettingsDialog(QDialog):
    def __init__(self, s: AppSettings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        self.ff = QLineEdit(s.ffmpeg_path)
        self.ff.setPlaceholderText("автоматически")
        self.demucs_model = QComboBox()
        self.demucs_model.addItem("htdemucs — быстрее", "htdemucs")
        self.demucs_model.addItem("htdemucs_ft — точнее, в 4 раза медленнее", "htdemucs_ft")
        self.demucs_model.addItem("mdx_extra — альтернатива", "mdx_extra")
        self.demucs_model.setCurrentIndex(max(0, self.demucs_model.findData(s.demucs_model)))
        self.device = QComboBox()
        self.device.addItem("Авто (видеокарта NVIDIA, если есть)", "auto")
        self.device.addItem("Только процессор", "cpu")
        self.device.setCurrentIndex(max(0, self.device.findData(s.demucs_device)))
        self.postroll = QDoubleSpinBox()
        self.postroll.setRange(0, 10)
        self.postroll.setSuffix(" с")
        self.postroll.setValue(s.record_postroll)
        self.ff_status = QLabel()
        self.ff_status.setWordWrap(True)

        f = QFormLayout()
        f.addRow("FFmpeg:", path_row(self.ff, "ffmpeg.exe", "ffmpeg (ffmpeg*);;Все файлы (*)", parent=self))
        f.addRow("", self.ff_status)
        f.addRow("Модель Demucs:", self.demucs_model)
        f.addRow("Устройство Demucs:", self.device)
        f.addRow("Запись после реплики:", self.postroll)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)
        self.ff.textChanged.connect(self._check)
        self._check()

    def _check(self, *_):
        ffmpeg.set_ffmpeg_path(self.ff.text().strip())
        try:
            self.ff_status.setText("Используется: " + ffmpeg.find_ffmpeg())
            self.ff_status.setStyleSheet("color:gray")
        except ffmpeg.FFmpegError as e:
            self.ff_status.setText(str(e))
            self.ff_status.setStyleSheet("color:#c62828")

    def _ok(self):
        self.s.ffmpeg_path = self.ff.text().strip()
        self.s.demucs_model = self.demucs_model.currentData()
        self.s.demucs_device = self.device.currentData()
        self.s.record_postroll = self.postroll.value()
        self.s.save()
        self.accept()


def ask_text(parent, title: str, label: str, text: str = "") -> Optional[str]:
    from PySide6.QtWidgets import QInputDialog
    t, ok = QInputDialog.getText(parent, title, label, text=text)
    return t if ok else None


__all__ = ["NewProjectDialog", "ImportSubsDialog", "MixPanel", "ExportDialog",
           "SettingsDialog", "VIDEO_FILTER", "SUB_FILTER", "Qt"]
