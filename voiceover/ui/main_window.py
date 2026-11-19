"""Главное окно VoiceOver Studio."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSplitter, QStyle, QTableView, QTabWidget, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget, QDialog,
                               QDialogButtonBox, QAbstractSpinBox)

from .. import APP_NAME, __version__
from ..core import ffmpeg, mixer
from ..core.models import Line, LineSource, MixMode, PROJECT_EXT, Project, line_status
from ..core.separation import demucs_available, separate_vocals
from ..core.settings import AppSettings
from ..core.subtitles import fmt_clock, write_srt
from ..core.synth import SynthResult, lines_needing_synth, preview_text, synthesize_lines
from .characters_panel import CharactersPanel, auto_assign_voice
from .dialogs import (SUB_FILTER, VIDEO_FILTER, ExportDialog, ImportSubsDialog, MixPanel,
                      NewProjectDialog, SettingsDialog)
from .lines_table import C_CHAR, C_TEXT, CharacterDelegate, LinesFilter, LinesModel
from .record_panel import RecordPanel
from .timeline import Timeline
from .video_player import VideoPlayer
from .workers import Task, pool

PROJECT_FILTER = f"Проект озвучки (*{PROJECT_EXT})"


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        ffmpeg.set_ffmpeg_path(settings.ffmpeg_path)
        self.project: Optional[Project] = None
        self.dirty = False
        self.task: Optional[Task] = None
        self._preview_range: Optional[tuple[float, float]] = None

        self.resize(1500, 900)
        self._build_ui()
        self._build_actions()
        self._update_title()
        self._update_actions()

        self.autosave = QTimer(self, interval=120_000, timeout=self._autosave)
        self.autosave.start()
        QApplication.instance().installEventFilter(self)

    # ================================================================ UI
    def _build_ui(self):
        self.player = VideoPlayer()
        self.timeline = Timeline()

        # Плеер отдельных реплик
        self.clip_player = QMediaPlayer(self)
        self.clip_audio = QAudioOutput(self)
        self.clip_player.setAudioOutput(self.clip_audio)

        # ---- вкладка «Реплики»
        self.lines_model = LinesModel(self)
        self.lines_filter = LinesFilter(self)
        self.lines_filter.setSourceModel(self.lines_model)
        self.table = QTableView()
        self.table.setModel(self.lines_filter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setItemDelegateForColumn(C_CHAR, CharacterDelegate(self.lines_model, self))
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(C_TEXT, QHeaderView.Stretch)
        for col, w in ((0, 50), (1, 85), (2, 85), (3, 120), (5, 95)):
            self.table.setColumnWidth(col, w)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту…")
        self.search.setClearButtonEnabled(True)
        self.f_char = QComboBox()
        self.f_char.setMinimumWidth(140)
        self.f_status = QComboBox()
        for label, key in (("Все статусы", "*"), ("Нет звука", "none"), ("Устарели", "stale"),
                           ("Синтез готов", "ok"), ("Записаны", "rec"), ("Без роли", "nochar"),
                           ("Выключены", "off")):
            self.f_status.addItem(label, key)
        filt = QHBoxLayout()
        filt.addWidget(self.search, 1)
        filt.addWidget(self.f_char)
        filt.addWidget(self.f_status)

        self.assign = QComboBox()
        self.assign.setToolTip("Назначить роль выделенным репликам")
        b_add = QPushButton("+ Реплика")
        b_add.setToolTip("Добавить реплику в позиции курсора (N)")
        b_del = QPushButton("Удалить")
        b_play = QPushButton("▶ Звук реплики")
        b_play.setToolTip("Прослушать синтез/запись выделенной реплики (P)")
        b_synth = QPushButton("Синтезировать выделенные")
        b_add.clicked.connect(self.add_line)
        b_del.clicked.connect(self.delete_lines)
        b_play.clicked.connect(self.play_selected_clip)
        b_synth.clicked.connect(lambda: self.synthesize(selected=True, force=True))
        acts = QHBoxLayout()
        acts.addWidget(b_add)
        acts.addWidget(b_del)
        acts.addWidget(QLabel("Роль:"))
        acts.addWidget(self.assign, 1)
        acts.addWidget(b_play)
        acts.addWidget(b_synth)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Текст выделенной реплики (можно редактировать)")
        self.text_edit.setMaximumHeight(70)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Заметка для актёра (интонация, ударение…)")
        self.stats = QLabel()
        self.stats.setStyleSheet("color:gray")

        lines_tab = QWidget()
        ll = QVBoxLayout(lines_tab)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addLayout(filt)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.text_edit)
        ll.addWidget(self.note_edit)
        ll.addLayout(acts)
        ll.addWidget(self.stats)

        # ---- остальные вкладки
        self.chars = CharactersPanel()
        self.record = RecordPanel(self.player, self.settings)
        self.mix_panel = MixPanel()
        self.mix_panel.on_change = self._on_mix_changed

        self.tabs = QTabWidget()
        self.tabs.addTab(lines_tab, "Реплики")
        self.tabs.addTab(self.chars, "Персонажи")
        self.tabs.addTab(self.record, "Запись")
        mix_wrap = QWidget()
        mw = QVBoxLayout(mix_wrap)
        mw.addWidget(self.mix_panel)
        mw.addStretch(1)
        self.tabs.addTab(mix_wrap, "Сведение")

        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.player)
        top.addWidget(self.tabs)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        top.setSizes([850, 650])
        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(self.timeline)
        main.setStretchFactor(0, 4)
        main.setStretchFactor(1, 1)
        main.setSizes([650, 220])
        self.setCentralWidget(main)

        # ---- строка состояния
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.progress_lbl = QLabel()
        self.cancel_btn = QToolButton(text="Отмена")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_task)
        sb = self.statusBar()
        sb.addPermanentWidget(self.progress_lbl)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

        # ---- сигналы
        self.player.positionChanged.connect(self.timeline.canvas.set_playhead)
        self.player.durationChanged.connect(self.timeline.set_duration)
        self.timeline.canvas.seekRequested.connect(self.player.seek)
        self.timeline.canvas.lineSelected.connect(self._select_line_from_timeline)
        self.timeline.canvas.lineActivated.connect(self._play_line_video)
        self.timeline.canvas.lineEdited.connect(self._on_line_edited)
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection)
        self.table.doubleClicked.connect(self._on_table_double)
        self.lines_model.edited.connect(self._on_line_edited)
        self.search.textChanged.connect(self._apply_filter)
        self.f_char.currentIndexChanged.connect(self._apply_filter)
        self.f_status.currentIndexChanged.connect(self._apply_filter)
        self.assign.activated.connect(self._assign_role)
        self.text_edit.textChanged.connect(self._text_edited)
        self.note_edit.editingFinished.connect(self._note_edited)
        self.chars.changed.connect(self._on_chars_changed)
        self.chars.listChanged.connect(self._on_chars_list_changed)
        self.chars.previewRequested.connect(self.preview_voice)
        self.chars.filterRequested.connect(self._filter_character)
        self.record.recorded.connect(self._on_line_edited)
        self.record.requestLine.connect(self._step_line)
        self.record.playTake.connect(self._play_take)

    def _build_actions(self):
        st = self.style()
        mb = self.menuBar()

        def act(text, slot, shortcut=None, icon=None, tip=None) -> QAction:
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            return a

        self.a_new = act("Новый проект…", self.new_project, "Ctrl+N", QStyle.SP_FileIcon)
        self.a_open = act("Открыть проект…", self.open_project_dialog, "Ctrl+O", QStyle.SP_DialogOpenButton)
        self.a_save = act("Сохранить", self.save_project, "Ctrl+S", QStyle.SP_DialogSaveButton)
        self.a_save_as = act("Сохранить как…", self.save_project_as, "Ctrl+Shift+S")
        self.a_import = act("Импорт субтитров…", self.import_subtitles, "Ctrl+I",
                            QStyle.SP_FileDialogDetailedView, "Загрузить реплики из SRT/ASS/VTT")
        self.a_export_srt = act("Экспорт сценария (SRT с ролями)…", self.export_script)
        self.a_export = act("Экспорт видео/звука…", self.export, "Ctrl+E", QStyle.SP_DialogApplyButton,
                            "Свести озвучку и сохранить видео с новой дорожкой")
        self.a_settings = act("Настройки…", self.open_settings)
        self.a_quit = act("Выход", self.close, "Ctrl+Q")
        self.a_separate = act("Отделить голоса (для дубляжа)", self.separate, None,
                              QStyle.SP_MediaVolumeMuted,
                              "Убрать оригинальные голоса, оставив музыку и звуки (Demucs)")
        self.a_synth_all = act("Синтезировать все реплики", lambda: self.synthesize(False), "F5",
                               QStyle.SP_BrowserReload, "Озвучить AI-голосами все реплики без звука")
        self.a_synth_sel = act("Синтезировать выделенные", lambda: self.synthesize(True, True),
                               "Ctrl+Return")
        self.a_preview = act("Превью: 1 мин от курсора", lambda: self.build_preview(60), "F6",
                             QStyle.SP_MediaPlay, "Свести фрагмент и слушать озвучку вместе с видео")
        self.a_preview_full = act("Превью: весь фильм", lambda: self.build_preview(None), "Shift+F6")
        self.a_auto_voices = act("Раздать голоса персонажам", self.chars.auto_voices)
        self.a_about = act("О программе", self.about)
        self.a_help = act("Как пользоваться", self.help, "F1")

        m = mb.addMenu("&Файл")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.recent_menu = m.addMenu("Недавние проекты")
        m.addSeparator()
        for a in (self.a_save, self.a_save_as):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_import, self.a_export_srt, self.a_export):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_settings)
        m.addAction(self.a_quit)
        m = mb.addMenu("&Обработка")
        for a in (self.a_separate, self.a_auto_voices, self.a_synth_all, self.a_synth_sel):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_preview)
        m.addAction(self.a_preview_full)
        m = mb.addMenu("&Справка")
        m.addAction(self.a_help)
        m.addAction(self.a_about)
        self._fill_recent()

        tb = self.addToolBar("Основное")
        tb.setObjectName("main_toolbar")
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_import, self.a_separate, self.a_synth_all, self.a_preview, self.a_export):
            tb.addAction(a)

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in self.settings.recent:
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda _=False, path=p: self.open_project(path))
        self.recent_menu.setEnabled(bool(self.settings.recent))

    # ================================================================ состояние
    def _update_title(self):
        name = Path(self.project.path).name if self.project and self.project.path else \
            ("без имени" if self.project else "")
        star = " *" if self.dirty else ""
        self.setWindowTitle(f"{name}{star} — {APP_NAME}" if name else APP_NAME)

    def _update_actions(self):
        has = self.project is not None
        busy = self.task is not None
        for a in (self.a_save, self.a_save_as, self.a_import, self.a_export, self.a_export_srt,
                  self.a_synth_all, self.a_synth_sel, self.a_preview, self.a_preview_full,
                  self.a_separate, self.a_auto_voices):
            a.setEnabled(has and not busy)
        self.a_new.setEnabled(not busy)
        self.a_open.setEnabled(not busy)

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_stats(self):
        if not self.project:
            self.stats.setText("")
            return
        counts: dict[str, int] = {}
        for ln in self.project.lines:
            s = line_status(self.project, ln)
            counts[s] = counts.get(s, 0) + 1
        total = len(self.project.lines)
        ready = counts.get("ok", 0) + counts.get("rec", 0)
        self.stats.setText(
            f"Реплик: {total} · готово: {ready} · нет звука: {counts.get('none', 0)} · "
            f"устарели: {counts.get('stale', 0)} · без роли: {counts.get('nochar', 0)}")

    def refresh_all(self):
        self.lines_model.reset()
        self.timeline.refresh()
        self._fill_char_combos()
        self._update_stats()

    def _fill_char_combos(self):
        cur_f = self.f_char.currentData() if self.f_char.count() else "*"
        self.f_char.blockSignals(True)
        self.f_char.clear()
        self.f_char.addItem("Все персонажи", "*")
        self.f_char.addItem("— без роли —", None)
        self.assign.clear()
        self.assign.addItem("— выбрать —", "__none__")
        self.assign.addItem("— без роли —", "")
        if self.project:
            for c in self.project.characters:
                self.f_char.addItem(c.name, c.id)
                self.assign.addItem(c.name, c.id)
            self.assign.addItem("+ Новый персонаж…", "__new__")
        i = self.f_char.findData(cur_f) if cur_f != "*" else 0
        self.f_char.setCurrentIndex(max(0, i))
        self.f_char.blockSignals(False)
        self._apply_filter()

    # ================================================================ проект
    def set_project(self, p: Optional[Project]):
        self.player.clear()
        self.project = p
        self.dirty = False
        self.lines_model.set_project(p)
        self.timeline.set_project(p)
        self.chars.set_project(p)
        self.record.set_project(p)
        bg = p.resolve(p.background_wav) if p else None
        self.mix_panel.set_mix(p.mix if p else None, bool(bg and bg.exists()))
        self._fill_char_combos()
        self._update_stats()
        if p and p.video_path:
            if Path(p.video_path).exists():
                self.player.load(p.video_path)
            else:
                QMessageBox.warning(self, "Видео не найдено",
                                    f"Файл видео не найден:\n{p.video_path}\n\n"
                                    "Укажите его заново.")
                path, _ = QFileDialog.getOpenFileName(self, "Где видео?", "", VIDEO_FILTER)
                if path:
                    p.video_path = path
                    self.player.load(path)
                    self.mark_dirty()
        self._update_title()
        self._update_actions()

    def maybe_save(self) -> bool:
        if not self.project or not self.dirty:
            return True
        r = QMessageBox.question(self, "Несохранённые изменения", "Сохранить изменения в проекте?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_project()
        return True

    def new_project(self, video: str = ""):
        if self.task or not self.maybe_save():
            return
        dlg = NewProjectDialog(self, video if isinstance(video, str) else "")
        if dlg.exec() != QDialog.Accepted or not dlg.info:
            return
        video = dlg.video.text().strip()
        vop = Path(video).with_suffix(PROJECT_EXT)
        if vop.exists():
            r = QMessageBox.question(
                self, "Проект уже есть",
                f"Рядом с видео уже есть проект:\n{vop.name}\n\nОткрыть его? "
                "(«Нет» — выбрать другое имя для нового проекта)",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Yes:
                self.open_project(str(vop))
                return
            if r == QMessageBox.Cancel:
                return
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект как", str(vop), PROJECT_FILTER)
            if not path:
                return
            vop = Path(path)
        p = Project(video_path=video, audio_stream=dlg.stream.currentData() or 0,
                    duration=dlg.info.duration, path=str(vop))
        p.mix.mode = dlg.mode
        p.project_dir.mkdir(parents=True, exist_ok=True)
        p.save()
        self.settings.add_recent(str(vop))
        self.settings.save()
        self._fill_recent()
        self.set_project(p)
        subs = dlg.subs.text().strip()

        def after_extract(_):
            self.statusBar().showMessage("Звук оригинала извлечён", 5000)
            if subs:
                self.import_subtitles(subs)
            elif p.mix.mode == MixMode.DUBBING:
                self.separate()

        self.extract_original(after_extract)

    def extract_original(self, then: Optional[Callable] = None):
        p = self.project
        if not p:
            return
        out = p.project_dir / "original.wav"

        def work(t: Task):
            ffmpeg.extract_audio(p.video_path, str(out), p.audio_stream, p.mix.sample_rate,
                                 p.duration, lambda f: t.report(f, "Извлечение звука"),
                                 t.is_cancelled)
            return str(out)

        def done(path):
            p.original_wav = p.relative(Path(path))
            self.mark_dirty()
            self.save_project()
            if then:
                then(path)

        self.run_task(Task("Извлечение звука", work), done)

    def open_project_dialog(self):
        if self.task or not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Открыть проект", "",
                                              PROJECT_FILTER + ";;Видео (для нового проекта) (*.mkv *.mp4 *.avi)")
        if path:
            if Path(path).suffix.lower() != PROJECT_EXT:
                self.new_project(path)
            else:
                self.open_project(path, ask=False)

    def open_project(self, path: str, ask: bool = True):
        if self.task:
            return
        if ask and not self.maybe_save():
            return
        try:
            p = Project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть проект:\n{e}")
            self.settings.recent = [x for x in self.settings.recent if x != path]
            self._fill_recent()
            return
        self.settings.add_recent(path)
        self.settings.save()
        self._fill_recent()
        self.set_project(p)
        orig = p.resolve(p.original_wav)
        if not orig or not orig.exists():
            if p.video_path and Path(p.video_path).exists():
                self.extract_original()

    def save_project(self) -> bool:
        if not self.project:
            return False
        if not self.project.path:
            return self.save_project_as()
        try:
            self.project.save()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return False
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage("Проект сохранён", 3000)
        return True

    def save_project_as(self) -> bool:
        if not self.project:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", self.project.path or "",
                                              PROJECT_FILTER)
        if not path:
            return False
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        old_dir = self.project.project_dir
        self.project.path = path
        new_dir = self.project.project_dir
        if old_dir.exists() and old_dir != new_dir and not new_dir.exists():
            import shutil
            shutil.copytree(old_dir, new_dir)
        self.settings.add_recent(path)
        self.settings.save()
        self._fill_recent()
        return self.save_project()

    def _autosave(self):
        if self.project and self.project.path and self.dirty and not self.task:
            self.save_project()

    # ================================================================ субтитры
    def import_subtitles(self, path: str = ""):
        if not self.project:
            return
        if not path or not isinstance(path, str):
            start = str(Path(self.project.video_path).parent) if self.project.video_path else ""
            path, _ = QFileDialog.getOpenFileName(self, "Импорт субтитров", start, SUB_FILTER)
            if not path:
                return
        dlg = ImportSubsDialog(path, bool(self.project.lines), self)
        if dlg.exec() != QDialog.Accepted:
            return
        p = self.project
        if dlg.replace.isChecked():
            p.lines.clear()
        shift = dlg.offset.value()
        new_chars = []
        for s in dlg.lines:
            cid = None
            if s.speaker:
                ch = p.character_by_name(s.speaker)
                if ch is None:
                    ch = p.add_character(s.speaker)
                    new_chars.append(ch)
                cid = ch.id
            p.lines.append(Line(max(0.0, s.start + shift), max(0.0, s.end + shift), s.text, cid))
        for ch in new_chars:
            auto_assign_voice(p, ch)
        if not p.characters:
            # Без ролей: создадим «Диктора», чтобы сразу можно было синтезировать
            ch = p.add_character("Диктор")
            auto_assign_voice(p, ch)
            for ln in p.lines:
                ln.character_id = ch.id
        p.sort_lines()
        self.chars.reload()
        self.refresh_all()
        self.mark_dirty()
        self.statusBar().showMessage(f"Импортировано реплик: {len(dlg.lines)}, "
                                     f"новых персонажей: {len(new_chars)}", 8000)

    def export_script(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт сценария",
                                              str(Path(self.project.path).with_suffix(".roles.srt")),
                                              "SRT (*.srt)")
        if not path:
            return
        items = []
        for ln in self.project.lines:
            ch = self.project.character(ln.character_id)
            items.append((ln.start, ln.end, ln.text, ch.name if ch else ""))
        write_srt(path, items)
        self.statusBar().showMessage(f"Сценарий сохранён: {path}", 5000)

    # ================================================================ реплики
    def selected_lines(self) -> list[Line]:
        if not self.project:
            return []
        rows = sorted({self.lines_filter.mapToSource(i).row()
                       for i in self.table.selectionModel().selectedRows()})
        return [self.project.lines[r] for r in rows]

    def current_line(self) -> Optional[Line]:
        idx = self.table.currentIndex()
        if not idx.isValid() or not self.project:
            sel = self.selected_lines()
            return sel[0] if sel else None
        return self.project.lines[self.lines_filter.mapToSource(idx).row()]

    def select_line(self, line_id: str, seek: bool = True):
        row = self.lines_model.row_of(line_id)
        if row < 0:
            return
        src = self.lines_model.index(row, 0)
        idx = self.lines_filter.mapFromSource(src)
        if not idx.isValid():   # отфильтрована — сбрасываем фильтр
            self.search.clear()
            self.f_char.setCurrentIndex(0)
            self.f_status.setCurrentIndex(0)
            idx = self.lines_filter.mapFromSource(src)
        self.table.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self.table.scrollTo(idx)
        if seek and not self.player.is_playing():
            self.player.seek(self.project.lines[row].start)

    def _on_table_selection(self, *_):
        sel = self.selected_lines()
        self.timeline.canvas.selected = {ln.id for ln in sel}
        ln = self.current_line()
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(ln.text if ln else "")
        self.text_edit.setEnabled(ln is not None)
        self.text_edit.blockSignals(False)
        self.note_edit.setText(ln.note if ln else "")
        if ln:
            self.timeline.canvas.ensure_visible(ln.start, ln.end)
            if not self.player.is_playing() and not self.record.is_recording():
                self.player.seek(ln.start)
        self._update_record_line()
        self.timeline.canvas.update()

    def _update_record_line(self):
        ln = self.current_line()
        if not self.project or ln is None:
            self.record.set_line(None)
            return
        lines = self.project.lines
        i = lines.index(ln)
        prev_t = lines[i - 1].text if i > 0 else ""
        next_t = lines[i + 1].text if i + 1 < len(lines) else ""
        self.record.set_line(ln, prev_t, next_t)

    def _select_line_from_timeline(self, line_id: str):
        self.select_line(line_id, seek=False)

    def _on_table_double(self, idx):
        pass  # редактирование выполняет делегат

    def _on_line_edited(self, line_id: str):
        self.lines_model.refresh_line(line_id)
        self.timeline.canvas.update()
        self._update_stats()
        self.mark_dirty()

    def _text_edited(self):
        ln = self.current_line()
        if ln is None:
            return
        ln.text = self.text_edit.toPlainText().strip()
        self._on_line_edited(ln.id)

    def _note_edited(self):
        ln = self.current_line()
        if ln is not None and ln.note != self.note_edit.text():
            ln.note = self.note_edit.text()
            self.mark_dirty()

    def _apply_filter(self, *_):
        self.lines_filter.set_filter(self.search.text(), self.f_char.currentData(),
                                     self.f_status.currentData())

    def _filter_character(self, cid: str):
        i = self.f_char.findData(cid)
        if i >= 0:
            self.f_char.setCurrentIndex(i)
        self.tabs.setCurrentIndex(0)

    def _assign_role(self, *_):
        data = self.assign.currentData()
        self.assign.setCurrentIndex(0)
        if data == "__none__" or not self.project:
            return
        lines = self.selected_lines()
        if not lines:
            QMessageBox.information(self, "Роль", "Сначала выделите реплики в таблице.")
            return
        if data == "__new__":
            from .dialogs import ask_text
            name = ask_text(self, "Новый персонаж", "Имя персонажа:")
            if not name or not name.strip():
                return
            ch = self.project.ensure_character(name.strip())
            auto_assign_voice(self.project, ch)
            data = ch.id
            self.chars.reload(ch.id)
        for ln in lines:
            ln.character_id = data or None
        self.lines_model.refresh_all()
        self._fill_char_combos()
        self.chars.reload()
        self.timeline.refresh()
        self._update_stats()
        self.mark_dirty()

    def add_line(self):
        if not self.project:
            return
        t = round(self.player.position(), 3)
        cur = self.current_line()
        cid = cur.character_id if cur else (self.project.characters[0].id
                                             if self.project.characters else None)
        ln = Line(t, t + 2.0, "", cid)
        self.project.lines.append(ln)
        self.project.sort_lines()
        self.refresh_all()
        self.select_line(ln.id, seek=False)
        self.tabs.setCurrentIndex(0)
        self.text_edit.setFocus()
        self.mark_dirty()

    def delete_lines(self):
        lines = self.selected_lines()
        if not lines or not self.project:
            return
        if len(lines) > 1 and QMessageBox.question(
                self, "Удалить", f"Удалить реплики: {len(lines)}?") != QMessageBox.Yes:
            return
        ids = {ln.id for ln in lines}
        self.project.lines = [ln for ln in self.project.lines if ln.id not in ids]
        self.refresh_all()
        self.mark_dirty()

    def _step_line(self, delta: int):
        """Перейти к соседней реплике (для записи — той же роли, если она задана)."""
        if not self.project or not self.project.lines:
            return
        cur = self.current_line()
        lines = self.project.lines
        i = lines.index(cur) if cur in lines else -1
        same_role = self.tabs.currentWidget() is self.record and cur is not None
        j = i
        while True:
            j += delta
            if j < 0 or j >= len(lines):
                return
            if not same_role or lines[j].character_id == cur.character_id:
                break
        self.select_line(lines[j].id)

    def _play_line_video(self, line_id: str):
        if not self.project:
            return
        ln = next((x for x in self.project.lines if x.id == line_id), None)
        if ln:
            self.player.play_range(max(0.0, ln.start - 0.3), ln.end + 0.3)

    def play_selected_clip(self):
        ln = self.current_line()
        if not ln or not self.project:
            return
        src, _, _ = mixer.clip_source(self.project, ln)
        if src is None:
            self.statusBar().showMessage("У реплики ещё нет звука — синтезируйте или запишите её", 4000)
            return
        self.play_file(str(src))

    def _play_take(self, line_id: str):
        """Прослушать запись вместе с видео: собираем превью вокруг реплики."""
        ln = next((x for x in self.project.lines if x.id == line_id), None) if self.project else None
        if ln:
            self.build_preview(None, (max(0.0, ln.start - 1.5), ln.end + 1.5), autoplay=True)

    def play_file(self, path: str):
        self.clip_player.stop()
        self.clip_player.setSource(QUrl())
        self.clip_player.setSource(QUrl.fromLocalFile(path))
        self.clip_audio.setVolume(0.9)
        self.clip_player.play()

    # ================================================================ персонажи
    def _on_chars_changed(self):
        self.lines_model.refresh_all()
        self.timeline.canvas.update()
        self._update_stats()
        self.mark_dirty()

    def _on_chars_list_changed(self):
        self._fill_char_combos()
        self.lines_model.refresh_all()
        self.timeline.refresh()
        self._update_stats()
        self.mark_dirty()

    def preview_voice(self, ch, text: str):
        if ch.source != LineSource.TTS:
            return
        tmp = Path(tempfile.gettempdir()) / "voiceover_preview"
        tmp.mkdir(exist_ok=True)
        base = str(tmp / f"preview_{ch.id}")
        engine, voice, rate, pitch = ch.engine.value, ch.voice, ch.rate, ch.pitch

        def work(t: Task):
            t.report(0.2, f"Синтез примера: {ch.name}")
            return preview_text(engine, voice, text, base, rate, pitch)

        self.run_task(Task("Прослушивание голоса", work), self.play_file, quiet=True)

    # ================================================================ задачи
    def run_task(self, task: Task, on_result: Optional[Callable] = None,
                 on_message: Optional[Callable] = None, quiet: bool = False):
        if self.task is not None:
            QMessageBox.information(self, "Подождите", f"Выполняется: {self.task.title}")
            return
        self.task = task
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        self.progress_lbl.setText(task.title)
        self._update_actions()

        def prog(f, text):
            self.progress.setValue(int(f * 1000))
            if text:
                self.progress_lbl.setText(text)

        def fin():
            self.task = None
            self.progress.hide()
            self.cancel_btn.hide()
            self.progress_lbl.setText("")
            self._update_actions()

        def err(msg):
            QMessageBox.critical(self, task.title, msg)

        task.signals.progress.connect(prog)
        task.signals.error.connect(err)
        task.signals.cancelled.connect(lambda: self.statusBar().showMessage("Отменено", 4000))
        if on_result:
            task.signals.result.connect(on_result)
        if on_message:
            task.signals.message.connect(on_message)
        task.signals.finished.connect(fin)
        pool().start(task)

    def cancel_task(self):
        if self.task:
            self.task.cancel()
            self.progress_lbl.setText("Отмена…")

    # ================================================================ обработка
    def separate(self):
        p = self.project
        if not p:
            return
        ok, why = demucs_available()
        if not ok:
            QMessageBox.warning(self, "Отделение голосов", why)
            return
        orig = p.resolve(p.original_wav)
        if not orig or not orig.exists():
            self.extract_original(lambda _: self.separate())
            return
        bg = p.project_dir / "background.wav"
        if bg.exists() and QMessageBox.question(
                self, "Отделение голосов",
                "Фон без голосов уже создан. Пересоздать?") != QMessageBox.Yes:
            return
        model, device = self.settings.demucs_model, self.settings.demucs_device

        def work(t: Task):
            separate_vocals(str(orig), str(bg), str(p.project_dir / "vocals.wav"),
                            model, device, t.report, t.is_cancelled)
            return str(bg)

        def done(path):
            p.background_wav = p.relative(Path(path))
            if p.mix.mode != MixMode.DUBBING and QMessageBox.question(
                    self, "Готово", "Голоса отделены. Переключить проект в режим дубляжа?"
            ) == QMessageBox.Yes:
                p.mix.mode = MixMode.DUBBING
            self.mix_panel.set_mix(p.mix, True)
            self.mark_dirty()
            self.save_project()
            self.statusBar().showMessage("Фон без голосов готов", 8000)

        if QMessageBox.question(
                self, "Отделение голосов",
                "Будет запущено отделение голосов от музыки и звуков (Demucs).\n\n"
                "На видеокарте NVIDIA это занимает несколько минут, на процессоре — "
                "примерно столько же, сколько длится видео, или дольше.\n"
                "При первом запуске скачивается модель (~80 МБ). Продолжить?") != QMessageBox.Yes:
            return
        self.run_task(Task("Отделение голосов", work), done)

    def synthesize(self, selected: bool = False, force: bool = False):
        p = self.project
        if not p:
            return
        src_lines = self.selected_lines() if selected else p.lines
        todo = lines_needing_synth(p, src_lines, force=force)
        if not todo:
            msg = "Нет реплик для синтеза."
            nochar = sum(1 for ln in src_lines if p.character(ln.character_id) is None and ln.text.strip())
            if nochar:
                msg += f"\nРеплик без роли: {nochar} — назначьте им персонажей."
            self.statusBar().showMessage(msg.replace("\n", " "), 6000)
            if selected:
                QMessageBox.information(self, "Синтез", msg + "\n(Реплики актёров с живой "
                                                              "записью не синтезируются.)")
            return
        errors: list[str] = []

        def work(t: Task):
            return synthesize_lines(p, todo, t.emit, t.report, t.is_cancelled)

        def on_msg(r: SynthResult):
            ln = next((x for x in p.lines if x.id == r.line_id), None)
            if ln is None:
                return
            if r.error:
                errors.append(f"{fmt_clock(ln.start)} {ln.text[:40]}: {r.error}")
            else:
                ln.tts_path, ln.tts_hash = r.path, r.hash
                self.lines_model.refresh_line(ln.id)
            self.mark_dirty()

        def done(n_err):
            self.timeline.canvas.update()
            self._update_stats()
            self.save_project()
            if errors:
                self.show_report("Ошибки синтеза", f"Не удалось озвучить реплик: {len(errors)}",
                                 errors)
            else:
                self.statusBar().showMessage(f"Синтезировано реплик: {len(todo)}", 6000)

        self.run_task(Task(f"Синтез ({len(todo)} реплик)", work), done, on_msg)

    def build_preview(self, seconds: Optional[float], rng: Optional[tuple[float, float]] = None,
                      autoplay: bool = False):
        p = self.project
        if not p:
            return
        if rng is None:
            if seconds is None:
                rng = (0.0, p.duration)
            else:
                t = self.player.position()
                rng = (max(0.0, t - 2.0), t + seconds)
        out = str(p.project_dir / "preview.wav")
        self.player.dub.setSource(QUrl())   # освободить файл

        def work(t: Task):
            return mixer.render_project(p, out, t.report, t.is_cancelled, rng[0], rng[1])

        def done(warnings):
            import soundfile as sf
            length = sf.info(out).duration
            self.player.set_preview(out, rng[0], length)
            self.statusBar().showMessage(
                f"Превью {fmt_clock(rng[0])}–{fmt_clock(rng[0] + length)} готово. "
                f"Звук плеера переключён на озвучку.", 8000)
            self.player.seek(rng[0] if autoplay or seconds is not None else self.player.position())
            if autoplay or seconds is not None:
                self.player.play()

        self.run_task(Task("Сборка превью", work), done)

    def export(self):
        p = self.project
        if not p:
            return
        if p.mix.mode == MixMode.DUBBING:
            bg = p.resolve(p.background_wav)
            if not bg or not bg.exists():
                QMessageBox.warning(self, "Экспорт", "Режим дубляжа: сначала выполните "
                                                     "«Обработка → Отделить голоса».")
                return
        missing = [ln for ln in p.lines if line_status(p, ln) in ("none", "stale")]
        if missing and QMessageBox.question(
                self, "Экспорт",
                f"У {len(missing)} реплик нет актуального звука (не синтезированы, не записаны "
                "или текст изменён).\nВсё равно экспортировать?") != QMessageBox.Yes:
            return
        default = str(Path(p.video_path).with_name(Path(p.video_path).stem + ".voiceover.mkv"))
        dlg = ExportDialog(default, self)
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        out = dlg.out.text().strip()
        kind = dlg.kind.currentData()
        if Path(out).resolve() == Path(p.video_path).resolve():
            QMessageBox.warning(self, "Экспорт", "Нельзя перезаписать исходное видео.")
            return
        mix_wav = str(p.project_dir / "mix.wav")

        def work(t: Task):
            warnings = mixer.render_project(p, mix_wav, lambda f, s: t.report(f * 0.5, s),
                                            t.is_cancelled)
            prog = lambda f: t.report(0.5 + f * 0.5, "Сборка файла (FFmpeg)")  # noqa: E731
            if kind == "video":
                ffmpeg.mux_video(p.video_path, mix_wav, out, opts, p.audio_stream, p.duration,
                                 prog, t.is_cancelled)
            else:
                ffmpeg.encode_audio(mix_wav, out, opts, p.duration, prog, t.is_cancelled)
            return warnings

        def done(warnings):
            self.save_project()
            self.show_report("Экспорт завершён", f"Файл сохранён:\n{out}", warnings,
                             "Предупреждения при сведении:")

        self.run_task(Task("Экспорт", work), done)

    def show_report(self, title: str, text: str, items: list[str], items_title: str = ""):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(640, 420 if items else 140)
        lay = QVBoxLayout(dlg)
        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(lbl)
        if items:
            if items_title:
                lay.addWidget(QLabel(items_title))
            te = QTextEdit()
            te.setReadOnly(True)
            te.setPlainText("\n".join(items))
            lay.addWidget(te, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _on_mix_changed(self):
        self.mark_dirty()

    # ================================================================ прочее
    def open_settings(self):
        SettingsDialog(self.settings, self).exec()
        ffmpeg.set_ffmpeg_path(self.settings.ffmpeg_path)

    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> {__version__}<br>"
                          "Многоголосая озвучка фильмов, сериалов, мультфильмов и аниме.<br><br>"
                          "Синтез: Edge TTS, Silero. Отделение голосов: Demucs. "
                          "Видео/звук: FFmpeg. Интерфейс: Qt (PySide6).")

    def help(self):
        QMessageBox.information(self, "Как пользоваться", HELP_TEXT)

    def eventFilter(self, obj, ev):
        # Горячие клавиши, которые не должны мешать вводу текста
        if ev.type() == QEvent.KeyPress and self.isActiveWindow() and self.project:
            fw = QApplication.focusWidget()
            typing = isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)) or \
                (isinstance(fw, QComboBox) and fw.isEditable()) or \
                (self.table.state() == QAbstractItemView.EditingState)
            if not typing and not ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                key = ev.key()
                if key == Qt.Key_Space:
                    if self.record.is_recording():
                        self.record.stop_recording()
                    else:
                        self.player.toggle()
                    return True
                if key == Qt.Key_R and self.tabs.currentWidget() is self.record:
                    self.record.start_recording()
                    return True
                if key == Qt.Key_P:
                    self.play_selected_clip()
                    return True
                if key == Qt.Key_N:
                    self.add_line()
                    return True
                if key == Qt.Key_Delete and fw is self.table:
                    self.delete_lines()
                    return True
                if key in (Qt.Key_Left, Qt.Key_Right) and fw is not self.table:
                    self.player.seek(self.player.position() + (-2 if key == Qt.Key_Left else 2))
                    return True
            if ev.modifiers() & Qt.ControlModifier and ev.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self.timeline.canvas.zoom(1.25)
                return True
            if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Minus:
                self.timeline.canvas.zoom(0.8)
                return True
        return super().eventFilter(obj, ev)

    def closeEvent(self, e):
        if self.task:
            if QMessageBox.question(self, "Выход", f"Выполняется «{self.task.title}». Прервать и выйти?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.task.cancel()
            pool().waitForDone(5000)
        if not self.maybe_save():
            e.ignore()
            return
        self.record.shutdown()
        self.settings.save()
        e.accept()


HELP_TEXT = """Порядок работы:

1. Файл → Новый проект: выберите видео, аудиодорожку и (по желанию) субтитры.
   Тип озвучки: закадровая (оригинал тихо) или дубляж (голоса удаляются).

2. Импорт субтитров (SRT/ASS/VTT). Персонажи берутся из поля Actor в ASS,
   из тега <v> в VTT или из префикса «Имя: текст» в SRT.

3. Вкладка «Персонажи»: для каждой роли выберите озвучку —
   AI-голос (движок, голос, темп, высота) или живую запись актёра.

4. Вкладка «Реплики»: правьте текст, тайминги и роли.
   Выделите реплики и выберите «Роль», чтобы назначить персонажа.

5. F5 — синтезировать AI-реплики. Вкладка «Запись» — записать живые роли:
   видео проигрывается с преролла, говорите, когда появится «ГОВОРИТЕ».

6. Для дубляжа: Обработка → Отделить голоса (Demucs).

7. F6 — превью минуты от курсора вместе с видео.
   Ctrl+E — экспорт видео с новой дорожкой.

Клавиши: Пробел — пуск/пауза, ←/→ — перемотка, P — звук реплики,
N — новая реплика, R — запись (на вкладке «Запись»), Del — удалить,
Ctrl+колесо на таймлайне — масштаб, двойной клик по блоку — проиграть.
"""
