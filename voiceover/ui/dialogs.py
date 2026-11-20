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
from . import theme

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
        self.info_lbl.setObjectName("muted")

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
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
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
                self.bg_status.setStyleSheet(f"color:{theme.current().ok}")
            else:
                self.bg_status.setText("⚠ Фон без голосов ещё не создан: "
                                       "меню «Обработка → Отделить голоса».")
                self.bg_status.setStyleSheet(f"color:{theme.current().warn}")
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
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
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
            self.ff_status.setStyleSheet(f"color:{theme.current().muted}")
        except ffmpeg.FFmpegError as e:
            self.ff_status.setText(str(e))
            self.ff_status.setStyleSheet(f"color:{theme.current().danger}")

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


# ======================================================================== распознавание

class TranscribeDialog(QDialog):
    """Параметры распознавания речи Whisper."""

    def __init__(self, s: AppSettings, has_vocals: bool, has_lines: bool,
                 cursor: float, duration: float, parent=None):
        super().__init__(parent)
        from ..core.transcribe import LANGUAGES, MODELS
        self.s = s
        self.setWindowTitle("Распознать речь (Whisper)")
        self.setMinimumWidth(560)
        self.duration = duration
        self.cursor = cursor

        self.source = QComboBox()
        if has_vocals:
            self.source.addItem("Голоса после «Отделить голоса» (точнее)", "vocals")
        self.source.addItem("Оригинальная дорожка", "original")
        self.language = QComboBox()
        for code, title in LANGUAGES:
            self.language.addItem(title, code or "")
        self.language.setCurrentIndex(max(0, self.language.findData(s.whisper_language)))
        self.model = QComboBox()
        for code, title in MODELS:
            self.model.addItem(title, code)
        self.model.setCurrentIndex(max(0, self.model.findData(s.whisper_model)))
        self.device = QComboBox()
        self.device.addItem("Авто (видеокарта NVIDIA, если есть)", "auto")
        self.device.addItem("Только процессор", "cpu")
        self.device.setCurrentIndex(max(0, self.device.findData(s.whisper_device)))
        self.range = QComboBox()
        self.range.addItem("Весь фильм", "all")
        self.range.addItem(f"С позиции курсора ({fmt_clock(cursor)}) до конца", "cursor")
        self.range.addItem("5 минут от курсора — для пробы", "5min")
        self.max_len = QDoubleSpinBox()
        self.max_len.setRange(2, 20)
        self.max_len.setSuffix(" с")
        self.max_len.setValue(s.whisper_max_len)
        self.max_len.setToolTip("Длинные фразы делятся на реплики по знакам препинания и паузам")
        self.replace = QCheckBox("Заменить реплики в выбранном диапазоне")
        self.replace.setChecked(True)
        self.replace.setVisible(has_lines)
        note = QLabel("Распознанные реплики добавляются без ролей — назначьте их в таблице "
                      "(выделите реплики → «Роль»). Модель скачивается при первом запуске.")
        note.setWordWrap(True)
        note.setObjectName("muted")

        f = QFormLayout()
        f.addRow("Источник звука:", self.source)
        f.addRow("Язык речи:", self.language)
        f.addRow("Модель:", self.model)
        f.addRow("Устройство:", self.device)
        f.addRow("Диапазон:", self.range)
        f.addRow("Макс. длина реплики:", self.max_len)
        f.addRow(self.replace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Распознать")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(note)
        lay.addWidget(bb)

    def _ok(self):
        self.s.whisper_language = self.language.currentData()
        self.s.whisper_model = self.model.currentData()
        self.s.whisper_device = self.device.currentData()
        self.s.whisper_max_len = self.max_len.value()
        self.s.save()
        self.accept()

    def time_range(self) -> tuple[float, Optional[float]]:
        r = self.range.currentData()
        if r == "cursor":
            return self.cursor, None
        if r == "5min":
            return self.cursor, min(self.duration, self.cursor + 300) if self.duration else self.cursor + 300
        return 0.0, None


# ======================================================================== перевод

class TranslateDialog(QDialog):
    """Перевод реплик: языки и какие реплики переводить."""

    def __init__(self, s: AppSettings, n_selected: int, n_total: int, parent=None):
        super().__init__(parent)
        from ..core.translate import LANGUAGES
        self.s = s
        self.setWindowTitle("Перевод реплик")
        self.setMinimumWidth(520)
        self.source = QComboBox()
        self.target = QComboBox()
        for code, title in LANGUAGES:
            self.source.addItem(title, code)
            if code != "auto":
                self.target.addItem(title, code)
        self.source.setCurrentIndex(max(0, self.source.findData(s.translate_source)))
        self.target.setCurrentIndex(max(0, self.target.findData(s.translate_target)))
        self.scope_sel = QRadioButton(f"Выделенные реплики ({n_selected})")
        self.scope_all = QRadioButton(f"Все реплики ({n_total})")
        self.scope_sel.setEnabled(n_selected > 0)
        (self.scope_sel if n_selected > 1 else self.scope_all).setChecked(True)
        self.skip_done = QCheckBox("Пропускать уже переведённые реплики")
        self.skip_done.setChecked(True)
        self.skip_done.setToolTip("Выключите, чтобы перевести заново — с сохранённого оригинала")
        note = QLabel("Используется Google Переводчик (нужен интернет, ключ не нужен). Реплики уходят "
                      "крупными пачками с паузами, чтобы Google не ограничивал запросы. Текст оригинала "
                      "сохраняется в реплике, вернуть его можно через контекстное меню таблицы "
                      "или «Отменить». Озвучка переведённых реплик станет «устаревшей».")
        note.setWordWrap(True)
        note.setObjectName("muted")
        f = QFormLayout()
        f.addRow("С языка:", self.source)
        f.addRow("На язык:", self.target)
        f.addRow("Что переводить:", self.scope_sel)
        f.addRow("", self.scope_all)
        f.addRow(self.skip_done)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Перевести")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(note)
        lay.addWidget(bb)

    def _ok(self):
        if self.source.currentData() == self.target.currentData():
            QMessageBox.warning(self, "Перевод", "Языки оригинала и перевода совпадают.")
            return
        self.s.translate_source = self.source.currentData()
        self.s.translate_target = self.target.currentData()
        self.s.save()
        self.accept()


# ======================================================================== OCR

class OcrDialog(QDialog):
    """Субтитры с видео: язык, область кадра с предпросмотром, диапазон."""

    def __init__(self, s: AppSettings, video: str, cursor: float, duration: float,
                 has_lines: bool, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QSpinBox
        from ..core import ocr
        self.s = s
        self.video = video
        self.cursor = cursor
        self.duration = duration
        self.setWindowTitle("Субтитры с видео (распознавание текста в кадре)")
        self.setMinimumWidth(720)
        self._frame = ocr.grab_frame(video, cursor if cursor > 0.5 else min(duration / 3, 60))

        self.language = QComboBox()
        for code, title, _ in ocr.LANGUAGES:
            self.language.addItem(title, code)
        self.language.setCurrentIndex(max(0, self.language.findData(s.ocr_language)))
        self.mode = QComboBox()
        for code, title in ocr.MODES:
            self.mode.addItem(title, code)
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.ocr_mode)))
        self.engine_lbl = QLabel()
        self.engine_lbl.setWordWrap(True)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 10)
        self.fps.setSingleStep(1)
        self.fps.setSuffix(" кадр/с")
        self.fps.setValue(s.ocr_fps)
        self.fps.setToolTip("Больше — точнее тайминги, но медленнее")
        self.region = []
        for label, val in zip(("Слева", "Сверху", "Ширина", "Высота"), s.ocr_region):
            sp = QSpinBox()
            sp.setRange(0, 100)
            sp.setSuffix(" %")
            sp.setValue(int(round(val * 100)))
            sp.setToolTip(f"{label}: доля кадра")
            sp.valueChanged.connect(self._update_preview)
            self.region.append(sp)
        self.range = QComboBox()
        self.range.addItem("Весь фильм", "all")
        self.range.addItem(f"С курсора ({fmt_clock(cursor)}) до конца", "cursor")
        self.range.addItem("2 минуты от курсора — для пробы", "2min")
        self.replace = QCheckBox("Заменить реплики в выбранном диапазоне")
        self.replace.setChecked(True)
        self.replace.setVisible(has_lines)
        self.preview = QLabel()
        self.preview.setMinimumHeight(200)
        self.preview.setAlignment(Qt.AlignCenter)

        reg = QHBoxLayout()
        for name, sp in zip(("слева", "сверху", "ширина", "высота"), self.region):
            lbl = QLabel(name)
            lbl.setObjectName("muted")
            reg.addWidget(lbl)
            reg.addWidget(sp)
        f = QFormLayout()
        f.addRow("Язык текста:", self.language)
        f.addRow("", self.engine_lbl)
        f.addRow("Вид субтитров:", self.mode)
        f.addRow("Область кадра:", reg)
        f.addRow("Частота проверки:", self.fps)
        f.addRow("Диапазон:", self.range)
        f.addRow(self.replace)
        note = QLabel("Рамка на кадре показывает, где искать текст. Подберите её так, чтобы субтитры "
                      "помещались целиком, а лишних надписей было поменьше.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Распознать")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.preview)
        lay.addWidget(note)
        lay.addLayout(f)
        lay.addWidget(bb)
        self.language.currentIndexChanged.connect(self._update_engine)
        self._update_engine()
        self._update_preview()

    def _update_engine(self, *_):
        from ..core import ocr
        name, hint = ocr.pick_engine(self.language.currentData())
        if name:
            self.engine_lbl.setText({"rapidocr": "Движок: RapidOCR (быстрый, без PyTorch)",
                                     "easyocr": "Движок: EasyOCR"}.get(name, name))
            self.engine_lbl.setStyleSheet(f"color:{theme.current().muted}")
        else:
            self.engine_lbl.setText(hint)
            self.engine_lbl.setStyleSheet(f"color:{theme.current().danger}")

    def region_values(self) -> list[float]:
        return [sp.value() / 100 for sp in self.region]

    def _update_preview(self, *_):
        from PySide6.QtGui import QImage, QPainter, QPen, QPixmap, QColor
        if self._frame is None:
            self.preview.setText("Не удалось получить кадр для предпросмотра")
            return
        h, w, _ = self._frame.shape
        img = QImage(self._frame.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        p = QPainter(pm)
        left, top, rw, rh = self.region_values()
        pen = QPen(QColor(theme.current().accent), 3)
        p.setPen(pen)
        p.drawRect(int(w * left), int(h * top), int(w * rw), int(h * rh))
        p.end()
        self.preview.setPixmap(pm.scaled(680, 383, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _ok(self):
        from ..core import ocr
        if ocr.pick_engine(self.language.currentData())[0] is None:
            QMessageBox.warning(self, "OCR", self.engine_lbl.text())
            return
        self.s.ocr_language = self.language.currentData()
        self.s.ocr_mode = self.mode.currentData()
        self.s.ocr_fps = self.fps.value()
        self.s.ocr_region = self.region_values()
        self.s.save()
        self.accept()

    def settings(self):
        from ..core.ocr import OcrRegion, OcrSettings
        left, top, w, h = self.region_values()
        return OcrSettings(language=self.s.ocr_language, region=OcrRegion(left, top, w, h),
                           fps=self.s.ocr_fps, mode=self.s.ocr_mode)

    def time_range(self) -> tuple[float, Optional[float]]:
        r = self.range.currentData()
        if r == "cursor":
            return self.cursor, None
        if r == "2min":
            return self.cursor, min(self.duration, self.cursor + 120) if self.duration else self.cursor + 120
        return 0.0, None
