"""Главное окно VoiceOver Studio."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                               QSplitter, QStackedWidget, QTabWidget, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import ffmpeg, mixer
from ..core.models import Line, MixMode, PROJECT_EXT, Project, line_status
from ..core.separation import demucs_available, separate_vocals
from ..core.settings import AppSettings, data_dir
from ..core.subtitles import fmt_clock, write_srt
from ..core.transcribe import transcribe, whisper_available
from ..core.undo import UndoHistory
from ..core.waveform import PEAKS_PER_SEC, compute_peaks
from . import theme
from .characters_panel import CharactersPanel, prepare_new_character
from .dialogs import (SUB_FILTER, VIDEO_FILTER, ExportDialog, ImportSubsDialog, MixPanel,
                      NewProjectDialog, SettingsDialog, TranscribeDialog)
from .lines_table import (C_CHAR, C_ORIG, C_STATUS, C_TEXT, LinesView, LinesFilter, LinesModel, RoleDelegate,
                          StatusDelegate)
from .record_panel import RecordPanel
from .timeline import Timeline
from .video_player import VideoPlayer
from .welcome import WelcomePage
from . import team_tools, text_tools
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
        self.history = UndoHistory()
        self._pending_label = ""
        self._restoring = False

        self.setWindowIcon(theme.app_icon())
        self.resize(1560, 940)
        self._build_ui()
        self.actor_win = None   # окно актёра (Ctrl+Shift+A)
        self._build_actions()
        self.apply_hotkeys()
        self._apply_icons()
        self._update_title()
        self._update_actions()

        self.commit_timer = QTimer(self, singleShot=True, interval=600, timeout=self._commit)
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

        # Строка «сейчас звучит» под видео
        # Сигнатура: реплика, которая звучит сейчас, под кадром — широким светлым срезом
        self.now_bar = QFrame()
        self.now_bar.setObjectName("cue")
        self.cue_who = QLabel()
        self.cue_who.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.cue_who.setObjectName("cueWho")
        self.now_lbl = QLabel()
        self.now_lbl.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.now_lbl.setObjectName("cueText")
        self.now_lbl.setWordWrap(True)
        self.now_lbl.setMinimumHeight(56)
        self.now_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        nl = QVBoxLayout(self.now_bar)
        nl.setContentsMargins(20, 12, 20, 14)
        nl.setSpacing(4)
        nl.addWidget(self.cue_who)
        nl.addWidget(self.now_lbl)
        left = QWidget()
        ll0 = QVBoxLayout(left)
        ll0.setContentsMargins(0, 0, 0, 0)
        ll0.setSpacing(0)
        ll0.addWidget(self.player, 1)
        from .rythmo import RythmoBand
        self.rythmo = RythmoBand()
        self.rythmo.setVisible(self.settings.rythmo_band)
        ll0.addWidget(self.rythmo)
        ll0.addWidget(self.now_bar)

        # ---- вкладка «Реплики»
        self.lines_model = LinesModel(self)
        self.lines_filter = LinesFilter(self)
        self.lines_filter.setSourceModel(self.lines_model)
        self.table = LinesView()
        self.table.setModel(self.lines_filter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setItemDelegateForColumn(C_CHAR, RoleDelegate(self.lines_model, self))
        self.table.setItemDelegateForColumn(C_STATUS, StatusDelegate(self))
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(C_TEXT, QHeaderView.Stretch)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col, w in ((0, 56), (1, 92), (2, 92), (3, 130), (5, 112), (6, 58)):
            self.table.setColumnWidth(col, w)
        self.table.setColumnWidth(C_ORIG, 240)
        self.table.setColumnHidden(C_ORIG, True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту…")
        self.search.setClearButtonEnabled(True)
        self.f_char = QComboBox()
        self.f_char.setMinimumWidth(150)
        self.f_status = QComboBox()
        for label, key in (("Все статусы", "*"), ("Нет записи", "none"),
                           ("Записаны", "rec"), ("Без роли", "nochar"),
                           ("Выключены", "off"), ("Слишком быстрые", "fast")):
            self.f_status.addItem(label, key)
        filt = QHBoxLayout()
        filt.addWidget(self.search, 1)
        filt.addWidget(self.f_char)
        filt.addWidget(self.f_status)

        self.assign = QComboBox()
        self.assign.setToolTip("Назначить роль выделенным репликам")
        self.assign.setMinimumWidth(150)
        self.b_add = QPushButton(" Реплика")
        self.b_add.setToolTip("Добавить реплику в позиции курсора (N)")
        self.b_del = QPushButton("")
        self.b_del.setToolTip("Удалить выделенные реплики (Del)")
        self.b_play = QPushButton(" Слушать")
        self.b_play.setToolTip("Прослушать запись выделенной реплики (P)")
        self.b_rec = QPushButton(" Записать")
        self.b_rec.setToolTip("Перейти к записи выделенной реплики (вкладка «Запись»)")
        self.b_add.clicked.connect(self.add_line)
        self.b_del.clicked.connect(self.delete_lines)
        self.b_play.clicked.connect(self.play_selected_clip)
        self.b_rec.clicked.connect(lambda: self.tabs.setCurrentWidget(self.record))
        acts = QHBoxLayout()
        acts.addWidget(self.b_add)
        acts.addWidget(self.b_del)
        acts.addSpacing(8)
        role_lbl = QLabel("Роль:")
        role_lbl.setObjectName("muted")
        acts.addWidget(role_lbl)
        acts.addWidget(self.assign, 1)
        acts.addSpacing(8)
        acts.addWidget(self.b_play)
        acts.addWidget(self.b_rec)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Текст выделенной реплики")
        self.text_edit.setMaximumHeight(72)
        self.text_edit.setToolTip("Ударение — Ctrl+' после гласной. Пометки для актёра — в "
                                  "скобках: (смеётся), [шёпотом]; они не произносятся.")
        from .spell_ui import SpellSupport
        self.spell = SpellSupport(self.text_edit, self.settings.spell_lang)
        self.orig_lbl = QLabel()
        self.orig_lbl.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.orig_lbl.setObjectName("muted")
        self.orig_lbl.setWordWrap(True)
        self.orig_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.orig_lbl.hide()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Заметка для актёра: интонация, ударение…")
        # Сводка готовности: числа отдельно, каждое со своей подписью
        self.stats = QWidget()
        sl = QHBoxLayout(self.stats)
        sl.setContentsMargins(0, 6, 0, 0)
        sl.setSpacing(28)
        self.stat_labels: dict[str, QLabel] = {}
        for key, caption in (("ready", "записано"), ("none", "нет записи"), ("nochar", "без роли")):
            box = QVBoxLayout()
            box.setSpacing(0)
            fig = QLabel("0")
            fig.setObjectName("figure")
            fig.setFont(theme.display_font(theme.TEXT_2XL, tabular=True))
            cap = QLabel(caption)
            cap.setObjectName("label")
            box.addWidget(fig)
            box.addWidget(cap)
            sl.addLayout(box)
            self.stat_labels[key] = fig
        sl.addStretch(1)

        lines_tab = QWidget()
        ll = QVBoxLayout(lines_tab)
        ll.setContentsMargins(16, 14, 16, 12)
        ll.setSpacing(8)
        ll.addLayout(filt)
        ll.addWidget(self.table, 1)
        ll.addWidget(self.text_edit)
        ll.addWidget(self.orig_lbl)
        ll.addWidget(self.note_edit)
        ll.addLayout(acts)
        ll.addWidget(self.stats)

        # ---- остальные вкладки
        self.chars = CharactersPanel()
        self.record = RecordPanel(self.player, self.settings)
        self.mix_panel = MixPanel()
        self.mix_panel.on_change = self._on_mix_changed

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.tabs.setIconSize(QSize(18, 18))
        self.tabs.addTab(lines_tab, "Реплики")
        self.tabs.addTab(self.chars, "Персонажи")
        self.tabs.addTab(self.record, "Запись")
        mix_wrap = QWidget()
        mw = QVBoxLayout(mix_wrap)
        mw.setContentsMargins(12, 12, 12, 12)
        mw.addWidget(self.mix_panel)
        mw.addStretch(1)
        self.tabs.addTab(mix_wrap, "Сведение")

        top = QSplitter(Qt.Horizontal)
        top.addWidget(left)
        top.addWidget(self.tabs)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)
        top.setSizes([860, 680])
        editor = QSplitter(Qt.Vertical)
        editor.addWidget(top)
        editor.addWidget(self.timeline)
        editor.setStretchFactor(0, 4)
        editor.setStretchFactor(1, 1)
        editor.setSizes([640, 240])


        self.welcome = WelcomePage()
        self.welcome.newRequested.connect(self.new_project)
        self.welcome.openRequested.connect(self.open_project_dialog)
        self.welcome.recentRequested.connect(lambda p: self.open_project(p))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(editor)
        self.setCentralWidget(self.stack)

        # ---- строка состояния
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(240)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.progress_lbl = QLabel()
        self.progress_lbl.setTextFormat(Qt.PlainText)   # текст реплик — не HTML
        self.cancel_btn = QToolButton(text="Отмена")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_task)
        sb = self.statusBar()
        sb.addPermanentWidget(self.progress_lbl)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

        # ---- сигналы
        self.player.positionChanged.connect(self.timeline.canvas.set_playhead)
        self.player.positionChanged.connect(self._update_now_line)
        self.player.positionChanged.connect(self.rythmo.set_time)
        self.player.playingChanged.connect(self.rythmo.set_playing)
        self.rythmo.seekRequested.connect(self.player.seek)
        self.player.durationChanged.connect(self.timeline.set_duration)
        self.timeline.canvas.seekRequested.connect(self.player.seek)
        self.timeline.canvas.lineSelected.connect(self._select_line_from_timeline)
        self.timeline.canvas.lineActivated.connect(self._play_line_video)
        self.timeline.canvas.muteToggled.connect(self.toggle_track_mute)
        self.timeline.canvas.markerClicked.connect(self.edit_marker)
        self.timeline.canvas.markerAddRequested.connect(self.add_marker)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.timeline.canvas.lineEdited.connect(lambda i: self._on_line_edited(i, "Изменение тайминга"))
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection)
        self.lines_model.edited.connect(lambda i: self._on_line_edited(i, "Правка реплики"))
        self.search.textChanged.connect(self._apply_filter)
        self.f_char.currentIndexChanged.connect(self._apply_filter)
        self.f_status.currentIndexChanged.connect(self._apply_filter)
        self.assign.activated.connect(self._assign_role)
        self.text_edit.textChanged.connect(self._text_edited)
        self.note_edit.editingFinished.connect(self._note_edited)
        self.chars.changed.connect(self._on_chars_changed)
        self.chars.listChanged.connect(self._on_chars_list_changed)
        self.chars.filterRequested.connect(self._filter_character)
        self.record.recorded.connect(lambda i: self._on_line_edited(i, "Запись реплики"))
        from .cue import CueOverlay
        self.cue_overlay = CueOverlay(self.player.video, self)
        self.record.cueState.connect(self.cue_overlay.set_state)
        self.record.alignRequested.connect(lambda i: self.align_recordings([i], quiet=False))
        self.record.requestLine.connect(self._step_line)
        self.record.playTake.connect(self._play_take)

    def _build_actions(self):
        mb = self.menuBar()

        def act(text, slot, shortcut=None, icon=None, tip=None) -> QAction:
            a = QAction(text, self)
            a.setProperty("iconName", icon)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip + (f" ({shortcut})" if shortcut else ""))
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            return a

        self.a_new = act("Новый проект…", self.new_project, "Ctrl+N", "new")
        self.a_open = act("Открыть проект…", self.open_project_dialog, "Ctrl+O", "open")
        self.a_save = act("Сохранить", self.save_project, "Ctrl+S", "save")
        self.a_save_as = act("Сохранить как…", self.save_project_as, "Ctrl+Shift+S")
        self.a_close = act("Закрыть проект", self.close_project, "Ctrl+W")
        self.a_import = act("Субтитры", self.import_subtitles, "Ctrl+I", "subs",
                            "Импорт реплик из SRT/ASS/VTT")
        self.a_whisper = act("Распознать", self.run_whisper, "Ctrl+R", "whisper",
                             "Распознать речь в фильме (Whisper) и создать реплики")
        self.a_translate = act("Перевести", lambda: text_tools.translate_lines(self), "Ctrl+Shift+T",
                               "translate", "Перевести реплики на язык озвучки")
        self.a_restore = act("Вернуть оригинал выделенных", lambda: text_tools.restore_original(self))
        self.a_export_srt = act("Экспорт субтитров (SRT/ASS)…", self.export_script, "Ctrl+Shift+E",
                                tip="Сохранить перевод и/или оригинал как субтитры для релиза")
        self.a_export = act("Экспорт", self.export, "Ctrl+E", "export",
                            "Свести озвучку и сохранить видео с новой дорожкой")
        self.a_settings = act("Настройки…", self.open_settings, None, "settings")
        self.a_cleanup = act("Удалить неиспользуемые файлы…", self.cleanup_files)
        self.a_split = act("Разрезать реплику по курсору", self.split_current, "Ctrl+K", None,
                           "Разделить реплику в позиции курсора видео; запись делится вместе с ней")
        self.a_merge = act("Склеить выделенные реплики", self.merge_selected, "Ctrl+J", None,
                           "Объединить выделенные реплики одного персонажа в одну")
        self.a_marker = act("Добавить метку…", lambda: self.add_marker(self.player.position()),
                            None, None, "Заметка режиссёра к моменту фильма (клавиша M)")
        self.a_marker_next = act("Следующая метка", lambda: self.goto_marker(1), "Alt+Right")
        self.a_marker_prev = act("Предыдущая метка", lambda: self.goto_marker(-1), "Alt+Left")
        self.a_backups = act("Восстановить из резервной копии…", self.restore_backup, None, None,
                             "Вернуть проект к одной из автоматических копий")
        self.a_stems = act("Экспорт по дорожкам (для Reaper)…", self.export_stems, None, None,
                           "Голоса каждого персонажа, фон и микс отдельными WAV + проект Reaper")
        self.a_pkg_export = act("Пакет для актёра…", lambda: team_tools.export_package(self),
                                tip="Проект и сценарий ролей для записи на удалёнке")
        self.a_pkg_open = act("Открыть пакет актёра…", lambda: team_tools.open_package(self))
        self.a_rec_send = act("Отправить записи…", lambda: team_tools.send_recordings(self),
                              tip="Записи ваших ролей в zip для режиссёра")
        self.a_script = act("Сценарий для актёров (PDF)…", lambda: team_tools.export_script(self),
                            None, None, "Реплики каждой роли с таймкодами и заметками, сводка по ролям")
        self.a_rec_import = act("Загрузить записи актёров…",
                                lambda: team_tools.import_recordings(self))
        self.a_quit = act("Выход", self.close, "Ctrl+Q")
        self.a_undo = act("Отменить", self.undo, QKeySequence.Undo, "undo")
        self.a_redo = act("Повторить", self.redo, "Ctrl+Y", "redo")
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.a_separate = act("Отделить голоса", self.separate, None, "separate",
                              "Убрать оригинальные голоса, оставив музыку и звуки (Demucs)")
        self.a_diarize = act("Роли по голосам…", self.diarize_roles, None, "users",
                             "Разложить реплики по персонажам по звучанию голоса")
        self.a_batch = act("Пакетная обработка серий…", self._open_batch, None, None,
                           "Извлечь звук, отделить голоса и экспортировать несколько серий подряд")
        self.a_align = act("Подогнать записи к оригиналу", lambda: self.align_recordings(),
                           "Ctrl+L", None, "Сдвинуть записи выделенных реплик так, чтобы слоги "
                           "и паузы совпали с голосом оригинала")
        self.a_actor = act("Окно актёра", self.toggle_actor_window, "Ctrl+Shift+A", "user",
                           "Видео с отсчётом, суфлёром и ритмо-лентой отдельным окном — "
                           "на второй монитор или во весь экран")
        self.a_actor.setCheckable(True)
        self.a_preview = act("Превью", lambda: self.build_preview(60), "F6", "preview",
                             "Свести минуту от курсора и слушать вместе с видео")
        self.a_preview_full = act("Превью всего фильма", lambda: self.build_preview(None), "Shift+F6")
        self.a_about = act("О программе", self.about)
        self.a_help = act("Как пользоваться", self.help, "F1", "help")
        self.a_zoom_in = act("Увеличить таймлайн", lambda: self.timeline.canvas.zoom(1.25), "Ctrl+=")
        self.a_zoom_out = act("Уменьшить таймлайн", lambda: self.timeline.canvas.zoom(0.8), "Ctrl+-")
        self.a_theme = act("Сменить тему", self.toggle_theme, "Ctrl+T", "moon",
                           "Тёмная / светлая тема")

        m = mb.addMenu("&Файл")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.recent_menu = m.addMenu("Недавние проекты")
        m.addSeparator()
        for a in (self.a_save, self.a_save_as, self.a_close):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_import, self.a_export_srt, self.a_export, self.a_stems, self.a_script):
            m.addAction(a)
        m.addSeparator()
        tm = m.addMenu("Актёры на удалёнке")
        for a in (self.a_pkg_export, self.a_rec_import):
            tm.addAction(a)
        tm.addSeparator()
        for a in (self.a_pkg_open, self.a_rec_send):
            tm.addAction(a)
        m.addSeparator()
        m.addAction(self.a_settings)
        m.addAction(self.a_quit)

        m = mb.addMenu("&Правка")
        m.addAction(self.a_undo)
        m.addAction(self.a_redo)
        m.addSeparator()
        m.addAction(self.a_split)
        m.addAction(self.a_merge)
        m.addSeparator()
        for a in (self.a_marker, self.a_marker_next, self.a_marker_prev):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_backups)
        m.addAction(self.a_cleanup)

        m = mb.addMenu("&Обработка")
        for a in (self.a_whisper, self.a_diarize, self.a_translate, self.a_restore, self.a_separate,
                  self.a_align):
            m.addAction(a)
        m.addAction(self.a_batch)
        m.addSeparator()
        m.addAction(self.a_preview)
        m.addAction(self.a_preview_full)

        m = mb.addMenu("&Вид")
        self.a_rythmo = QAction("Ритмо-лента под видео", self, checkable=True)
        self.a_rythmo.setShortcut("Ctrl+B")
        self.a_rythmo.setChecked(self.settings.rythmo_band)
        self.a_rythmo.toggled.connect(self._toggle_rythmo)
        self.addAction(self.a_rythmo)
        m.addAction(self.a_rythmo)
        a_hk = QAction("Горячие клавиши…", self)
        a_hk.triggered.connect(self.edit_hotkeys)
        m.addAction(a_hk)
        m.addSeparator()
        grp = QActionGroup(self)
        self.theme_actions = {}
        for key, title in (("dark", "Тёмная тема"), ("light", "Светлая тема")):
            a = QAction(title, self, checkable=True)
            a.setChecked(self.settings.theme == key)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            grp.addAction(a)
            m.addAction(a)
            self.theme_actions[key] = a
        m.addSeparator()
        m.addAction(self.a_zoom_in)
        m.addAction(self.a_zoom_out)

        m = mb.addMenu("&Справка")
        m.addAction(self.a_help)
        m.addAction(self.a_about)
        self._fill_recent()

        tb = self.addToolBar("Основное")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.addSeparator()
        for a in (self.a_undo, self.a_redo):
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)
        for title, actions in (("Текст", (self.a_import, self.a_whisper, self.a_translate)),
                               ("Звук", (self.a_separate,)),
                               ("Проверка", (self.a_preview, self.a_actor))):
            tb.addSeparator()
            lbl = QLabel(title)
            lbl.setObjectName("groupLabel")
            tb.addWidget(lbl)
            for a in actions:
                tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.a_theme)
        tb.widgetForAction(self.a_theme).setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.addAction(self.a_export)
        tb.widgetForAction(self.a_export).setObjectName("primaryAction")
        self.toolbar = tb

    def _fit_toolbar(self):
        """Если кнопки с подписями не помещаются, второстепенные показываются только иконками
        (подсказка остаётся), чтобы «Экспорт» всегда был виден."""
        tb = getattr(self, "toolbar", None)
        if tb is None:
            return
        keep = {self.a_export}
        buttons = []
        for a in tb.actions():
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton) and a not in keep and a.text() and \
                    a not in (self.a_new, self.a_open, self.a_save, self.a_undo, self.a_redo,
                              getattr(self, "a_theme", None)):
                buttons.append(w)
        for w in buttons:
            w.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        need = tb.sizeHint().width()
        if need > self.width():
            for w in buttons:
                w.setToolButtonStyle(Qt.ToolButtonIconOnly)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_toolbar()

    def _apply_icons(self):
        th = theme.current()
        for a in self.findChildren(QAction):
            name = a.property("iconName")
            if name:
                color = th.accent_text if a is self.a_export else None
                a.setIcon(theme.icon(name, color))
        self.a_theme.setIcon(theme.icon("sun" if th.dark else "moon"))
        self.b_add.setIcon(theme.icon("plus"))
        self.b_del.setIcon(theme.icon("trash"))
        self.b_play.setIcon(theme.icon("headphones"))
        self.b_rec.setIcon(theme.icon("mic"))
        self.tabs.setTabIcon(0, theme.icon("list"))
        self.tabs.setTabIcon(1, theme.icon("users"))
        self.tabs.setTabIcon(2, theme.icon("mic"))
        self.tabs.setTabIcon(3, theme.icon("sliders"))
        self.player.apply_icons()
        self.welcome.apply_icons()
        self.welcome.set_recent(self.settings.recent)

    def set_theme(self, name: str):
        self.settings.theme = name
        self.settings.save()
        theme.apply_theme(QApplication.instance(), name)
        if name in self.theme_actions:
            self.theme_actions[name].setChecked(True)
        self._apply_icons()
        self.chars.reload()
        self.timeline.canvas.update()
        self.table.viewport().update()
        self._update_now_line(self.player.position())

    def toggle_theme(self):
        self.set_theme("light" if theme.current().dark else "dark")

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in self.settings.recent:
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda _=False, path=p: self.open_project(path))
        self.recent_menu.setEnabled(bool(self.settings.recent))
        self.welcome.set_recent(self.settings.recent)

    # ================================================================ состояние
    def _update_title(self):
        name = Path(self.project.path).stem if self.project and self.project.path else \
            ("без имени" if self.project else "")
        star = " •" if self.dirty else ""
        prog = getattr(self, "_progress", None)
        prog_s = f" — записано {prog:.0%}" if (self.project and prog is not None) else ""
        self.setWindowTitle(f"{name}{star}{prog_s} — {APP_NAME}" if name else APP_NAME)

    def _update_actions(self):
        has = self.project is not None
        busy = self.task is not None
        for a in (self.a_save, self.a_save_as, self.a_close, self.a_import, self.a_export,
                  self.a_export_srt, self.a_preview,
                  self.a_preview_full, self.a_separate, self.a_whisper,
                  self.a_cleanup, self.a_translate, self.a_restore, self.a_pkg_export,
                  self.a_rec_send, self.a_rec_import, self.a_align, self.a_stems,
                  self.a_diarize, self.a_script, self.a_backups, self.a_split,
                  self.a_merge, self.a_marker, self.a_marker_next, self.a_marker_prev):
            a.setEnabled(has and not busy)
        self.a_new.setEnabled(not busy)
        self.a_open.setEnabled(not busy)
        self.a_pkg_open.setEnabled(not busy)
        self.a_batch.setEnabled(not busy)
        self._update_undo_actions()

    def _update_undo_actions(self):
        busy = self.task is not None
        ok_u = bool(self.project) and not busy and (self.history.can_undo() or bool(self._pending_label))
        ok_r = bool(self.project) and not busy and self.history.can_redo()
        self.a_undo.setEnabled(ok_u)
        self.a_redo.setEnabled(ok_r)
        ul = self._pending_label or self.history.undo_label()
        self.a_undo.setText(f"Отменить: {ul}" if ok_u and ul else "Отменить")
        self.a_redo.setText(f"Повторить: {self.history.redo_label()}" if ok_r else "Повторить")
        self.a_undo.setToolTip(self.a_undo.text() + " (Ctrl+Z)")
        self.a_redo.setToolTip(self.a_redo.text() + " (Ctrl+Y)")

    def mark_dirty(self, label: str = "Изменение"):
        """Вызывается после любого изменения проекта: помечает несохранённым и
        откладывает фиксацию шага истории (быстрые правки склеиваются)."""
        if self._restoring or not self.project:
            return
        if not self.dirty:
            self.dirty = True
            self._update_title()
        self._pending_label = label
        self.commit_timer.start()
        self._update_undo_actions()

    def _commit(self):
        if self.project and self._pending_label:
            self.history.commit(self.project, self._pending_label)
        self._pending_label = ""
        self._update_undo_actions()

    def flush_history(self):
        if self.commit_timer.isActive():
            self.commit_timer.stop()
        self._commit()

    def undo(self):
        self._undo_redo(True)

    def redo(self):
        self._undo_redo(False)

    def _undo_redo(self, undo: bool):
        if not self.project or self.task or self.record.is_recording():
            return
        self.flush_history()
        cur = self.current_line()
        cur_id = cur.id if cur else None
        label = self.history.undo(self.project) if undo else self.history.redo(self.project)
        if label is None:
            return
        self._restoring = True
        try:
            self.chars.reload()
            self.refresh_all()
            bg = self.project.resolve(self.project.background_wav)
            self.mix_panel.set_mix(self.project.mix, bool(bg and bg.exists()))
            self._apply_track_mutes()
            if cur_id and self.lines_model.row_of(cur_id) >= 0:
                self.select_line(cur_id, seek=False)
            else:
                self._on_table_selection()
        finally:
            self._restoring = False
        self.dirty = True
        self._update_title()
        self._update_undo_actions()
        self.statusBar().showMessage(("Отменено: " if undo else "Повторено: ") + label, 4000)

    def _update_stats(self):
        if not self.project:
            for lbl in self.stat_labels.values():
                lbl.setText("0")
            return
        counts: dict[str, int] = {}
        for ln in self.project.lines:
            st = line_status(self.project, ln)
            counts[st] = counts.get(st, 0) + 1
        total = len(self.project.lines)
        ready = counts.get("rec", 0)
        self.stat_labels["ready"].setText(f"{ready} из {total}")
        todo = sum(1 for ln in self.project.lines if ln.enabled and ln.text.strip())
        self._progress = ready / todo if todo else None
        self._update_title()
        for key in ("none", "nochar"):
            self.stat_labels[key].setText(str(counts.get(key, 0)))
        th = theme.current()
        for key, col in (("none", th.error),):
            self.stat_labels[key].setStyleSheet(f"color:{col}" if counts.get(key) else "")

    def _toggle_rythmo(self, on: bool):
        self.settings.rythmo_band = on
        self.settings.save()
        self.rythmo.setVisible(on)
        self.rythmo.set_playing(self.player.is_playing())

    def refresh_all(self):
        self.lines_model.reset()
        has_orig = bool(self.project) and any(ln.orig_text for ln in self.project.lines)
        self.table.setColumnHidden(C_ORIG, not has_orig)
        self.timeline.refresh()
        self.rythmo.update()
        self._fill_char_combos()
        self._update_stats()
        self._update_now_line(self.player.position())

    def _fill_char_combos(self):
        if self.project:   # имена персонажей — известные проверке орфографии слова
            self.spell.set_known_words([c.name for c in self.project.characters])
        cur_f = self.f_char.currentData() if self.f_char.count() else "*"
        self.f_char.blockSignals(True)
        self.f_char.clear()
        self.f_char.addItem("Все персонажи", "*")
        self.f_char.addItem("— без роли —", None)
        self.assign.clear()
        self.assign.addItem("— выбрать —", "__none__")
        self.assign.addItem("— без роли —", "")
        if self.project:
            from .characters_panel import color_icon
            for c in self.project.characters:
                self.f_char.addItem(color_icon(c.color), c.name, c.id)
                self.assign.addItem(color_icon(c.color), c.name, c.id)
            self.assign.addItem("+ Новый персонаж…", "__new__")
        i = self.f_char.findData(cur_f) if cur_f != "*" else 0
        self.f_char.setCurrentIndex(max(0, i))
        self.f_char.blockSignals(False)
        self._apply_filter()

    def _update_now_line(self, t: float):
        if not self.project:
            self.cue_who.setText("")
            self.now_lbl.setText("")
            return
        active = []
        for ln in self.project.lines:
            if ln.start > t:
                break
            if ln.start <= t <= ln.end and ln.enabled:
                active.append(ln)
        if not active:
            self.cue_who.setText("сейчас без реплик")
            self.cue_who.setStyleSheet(f"color:{theme.current().muted}")
            self.now_lbl.setText("")
            return
        ln = active[-1]
        ch = self.project.character(ln.character_id)
        who = ch.name if ch else "без роли"
        if len(active) > 1:
            others = [self.project.character(x.character_id) for x in active[:-1]]
            who += ", одновременно с " + ", ".join(o.name if o else "без роли" for o in others)
        self.cue_who.setText(who)
        # цвет роли — данные проекта: он связывает реплику с её дорожкой на таймлайне
        self.cue_who.setStyleSheet(f"color:{theme.role_text(ch.color)}" if ch
                                   else f"color:{theme.current().muted}")
        self.now_lbl.setText(ln.text)

    # ================================================================ проект
    def set_project(self, p: Optional[Project]):
        self.player.clear()
        self.project = p
        self.dirty = False
        self._pending_label = ""
        self.history.reset(p)
        self.lines_model.set_project(p)
        self.timeline.set_project(p)
        self.rythmo.set_project(p)
        if self.actor_win is not None:
            self.actor_win.set_project(p)
        self.chars.set_project(p)
        self.record.set_project(p)
        bg = p.resolve(p.background_wav) if p else None
        self.mix_panel.set_mix(p.mix if p else None, bool(bg and bg.exists()))
        self._fill_char_combos()
        self._update_stats()
        self.stack.setCurrentIndex(1 if p else 0)
        if p and p.video_path:
            if Path(p.video_path).exists():
                self.player.load(p.video_path)
            else:
                QMessageBox.warning(self, "Видео не найдено",
                                    f"Файл видео не найден:\n{p.video_path}\n\nУкажите его заново.")
                path, _ = QFileDialog.getOpenFileName(self, "Где видео?", "", VIDEO_FILTER)
                if path:
                    p.video_path = path
                    self.player.load(path)
                    self.dirty = True
        self._load_waveform()
        self._update_now_line(0.0)
        self._update_title()
        self._update_actions()

    def _load_waveform(self):
        """Дорожки звука на таймлайне и в плеере: оригинал, а после отделения голосов —
        отдельно голоса и фон. Волны считаются в фоне (не блокируют интерфейс)."""
        p = self.project
        if not p:
            return
        keys = mixer.wave_keys(p)
        stems = mixer.stem_paths(p)
        files = ({"background": stems[0], "vocals": stems[1]} if stems
                 else {"original": p.resolve(p.original_wav)})
        self.timeline.canvas.set_waves(keys)
        self.timeline.refresh()
        self.player.set_stems({k: str(v) for k, v in files.items()} if stems else {})
        self.mix_panel.set_tracks(keys)
        self._apply_track_mutes()
        cache = p.project_dir / "cache"
        self._wave_tasks = []
        for key in keys:
            wav = files.get(key)
            if not wav or not wav.exists():
                continue

            def work(t: Task, wav=wav):
                return compute_peaks(str(wav), PEAKS_PER_SEC, cache, t.is_cancelled)

            task = Task("Волна", work)
            task.signals.result.connect(
                lambda pk, key=key: self.project is p
                and self.timeline.canvas.set_wave(key, pk, PEAKS_PER_SEC))
            self._wave_tasks.append(task)
            pool().start(task)

    # ------------------------------------------------------------ заглушение дорожек
    def _apply_track_mutes(self):
        p = self.project
        muted = set(p.mix.muted) if p else set()
        self.timeline.canvas.set_muted(muted)
        self.player.set_muted_tracks(muted)
        self.mix_panel.set_muted(muted)

    def toggle_track_mute(self, key: str):
        p = self.project
        if not p:
            return
        muted = set(p.mix.muted)
        muted ^= {key}
        p.mix.muted = sorted(muted)
        self._apply_track_mutes()
        self.mark_dirty("Заглушение дорожки")
        self._mutes_changed()

    def _on_mix_changed(self):
        self._apply_track_mutes()
        self.mark_dirty("Настройки сведения")

    def _mutes_changed(self):
        if self.timeline.canvas.preview_range:
            self.statusBar().showMessage(
                "Заглушение слышно сразу в режиме «Звук: оригинал». Чтобы оно попало в "
                "превью озвучки, соберите его заново (F6).", 8000)

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

    def close_project(self):
        if self.task or not self.maybe_save():
            return
        self.set_project(None)

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
                self.open_project(str(vop), ask=False)
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
            elif QMessageBox.question(
                    self, "Текст реплик",
                    "Субтитры не выбраны. Распознать речь в фильме с помощью Whisper?\n\n"
                    "(Можно сделать позже: «Обработка → Распознать» или импортировать субтитры.)"
            ) == QMessageBox.Yes:
                self.run_whisper()

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
            self.save_project()
            self._load_waveform()
            if then:
                then(path)

        self.run_task(Task("Извлечение звука", work), done)

    def open_project_dialog(self):
        if self.task or not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть проект", "",
            PROJECT_FILTER + ";;Видео (для нового проекта) (*.mkv *.mp4 *.avi *.mov *.webm)")
        if path:
            if Path(path).suffix.lower() != PROJECT_EXT:
                self.dirty = False
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
        if (not orig or not orig.exists()) and p.video_path and Path(p.video_path).exists():
            self.extract_original()

    def save_project(self) -> bool:
        if not self.project:
            return False
        if not self.project.path:
            return self.save_project_as()
        self.flush_history()
        try:
            from ..core.backup import make_backup
            make_backup(self.project)
        except Exception:  # noqa: BLE001  резервная копия не должна мешать сохранению
            pass
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

    def cleanup_files(self):
        """Удалить записи, на которые не ссылаются ни проект, ни история отмены
        (и старые файлы синтеза из проектов прежних версий)."""
        p = self.project
        if not p:
            return
        self.flush_history()
        used: set[str] = set()
        states = [p.snapshot()] + list(self.history.states())
        for st in states:
            for ln in st["lines"]:
                for key in ("rec_path",):
                    if ln.get(key):
                        used.add(str(p.resolve(ln[key]).resolve()))
        junk = []
        for sub in ("tts", "rec"):
            d = p.project_dir / sub
            if d.exists():
                junk += [f for f in d.iterdir() if f.is_file() and str(f.resolve()) not in used]
        cache = p.project_dir / "cache"
        cache_files = list(cache.glob("*.wav")) if cache.exists() else []
        size = sum(f.stat().st_size for f in junk + cache_files) / 1e6
        if not junk and not cache_files:
            QMessageBox.information(self, "Очистка", "Неиспользуемых файлов нет.")
            return
        if QMessageBox.question(
                self, "Очистка",
                f"Будут удалены дубли, не выбранные для сведения ({len(junk)} файлов), и кэш сведения "
                f"({len(cache_files)}), всего {size:.0f} МБ.\nПродолжить?") != QMessageBox.Yes:
            return
        for f in junk + cache_files:
            f.unlink(missing_ok=True)
        for ln in p.lines:   # забыть сдвиги удалённых дублей
            ln.takes = {k: v for k, v in ln.takes.items() if p.resolve(k).exists()}
        self.refresh_all()
        self.statusBar().showMessage(f"Освобождено {size:.0f} МБ", 6000)

    # ================================================================ субтитры и распознавание
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
            prepare_new_character(p, ch)
        if not p.characters:
            # Без ролей: создадим «Диктора», чтобы сразу можно было синтезировать
            ch = p.add_character("Диктор")
            prepare_new_character(p, ch)
            for ln in p.lines:
                ln.character_id = ch.id
        p.sort_lines()
        self.chars.reload()
        self.refresh_all()
        self.mark_dirty("Импорт субтитров")
        self.statusBar().showMessage(f"Импортировано реплик: {len(dlg.lines)}, "
                                     f"новых персонажей: {len(new_chars)}", 8000)

    def run_whisper(self):
        p = self.project
        if not p:
            return
        ok, why = whisper_available()
        if not ok:
            QMessageBox.warning(self, "Распознавание речи", why)
            return
        orig = p.resolve(p.original_wav)
        if not orig or not orig.exists():
            self.extract_original(lambda _: self.run_whisper())
            return
        vocals = p.project_dir / "vocals.wav"
        dlg = TranscribeDialog(self.settings, vocals.exists(), bool(p.lines),
                               self.player.position(), p.duration, self)
        if dlg.exec() != QDialog.Accepted:
            return
        src = str(vocals if dlg.source.currentData() == "vocals" else orig)
        t_from, t_to = dlg.time_range()
        replace = dlg.replace.isChecked()
        s = self.settings
        lang = s.whisper_language or None
        root = str(data_dir() / "models" / "whisper")

        def work(t: Task):
            return transcribe(src, s.whisper_model, lang, s.whisper_device, t_from, t_to,
                              s.whisper_max_len, root, t.report, t.is_cancelled)

        def done(res):
            lines, detected = res
            end = t_to if t_to is not None else float("inf")
            if replace:
                p.lines = [ln for ln in p.lines if ln.end <= t_from or ln.start >= end]
            from ..core.rythmo import words_to_fractions
            for sl in lines:
                p.lines.append(Line(sl.start, sl.end, sl.text, None,
                                    words=words_to_fractions(sl.start, sl.end, sl.words)))
            p.sort_lines()
            self.chars.reload()
            self.refresh_all()
            self.mark_dirty("Распознавание речи")
            self.save_project()
            self.tabs.setCurrentIndex(0)
            QMessageBox.information(
                self, "Распознавание завершено",
                f"Распознано реплик: {len(lines)} (язык: {detected or '?'}).\n\n"
                "Дальше: выделите реплики одного персонажа (Ctrl/Shift+клик) и выберите "
                "его в списке «Роль». Текст можно править прямо в таблице.")

        self.run_task(Task("Распознавание речи", work), done)

    def export_script(self):
        """Субтитры для релиза (перевод/оригинал, SRT или ASS)."""
        from ..core.subtitles import build_subtitle_items, write_ass
        from .dialogs import SubtitleExportDialog
        p = self.project
        if not p:
            return
        base = Path(p.video_path or p.path)
        lang = self.settings.translate_target or "ru"
        dlg = SubtitleExportDialog(str(base.with_name(f"{base.stem}.{lang}.ass")),
                                   any(ln.orig_text for ln in p.lines), self)
        if dlg.exec() != QDialog.Accepted:
            return
        out = dlg.out.text().strip()
        if not out:
            return
        if p.video_path and Path(out).resolve() == Path(p.video_path).resolve():
            QMessageBox.warning(self, "Экспорт субтитров", "Нельзя перезаписать видео.")
            return
        if Path(out).exists() and QMessageBox.question(
                self, "Экспорт субтитров", f"Файл уже существует:\n{out}\n\nЗаменить его?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        items = build_subtitle_items(p.lines, p.character, dlg.text.currentData(),
                                     dlg.speaker.isChecked(), dlg.only_enabled.isChecked())
        try:
            if dlg.fmt.currentData() == "ass":
                write_ass(out, items, base.stem, dlg.color.isChecked())
            else:
                write_srt(out, [i[:4] for i in items], with_speaker=dlg.speaker.isChecked())
        except OSError as e:
            QMessageBox.critical(self, "Экспорт субтитров", f"Не удалось сохранить файл:\n{e}")
            return
        self.statusBar().showMessage(f"Субтитры сохранены ({len(items)} реплик): {out}", 8000)

    # ================================================================ реплики
    def restore_backup(self):
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from ..core.backup import list_backups, load_backup_state
        p = self.project
        if not p:
            return
        items = list_backups(p)
        if not items:
            QMessageBox.information(self, "Резервные копии",
                                    "Копий пока нет. Они создаются автоматически при сохранении "
                                    "проекта (не чаще раза в 5 минут, хранятся последние 30).")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Восстановить из резервной копии")
        lay = QVBoxLayout(dlg)
        lbl = QLabel("Выберите копию. Восстановление можно отменить (Ctrl+Z); записи актёров "
                     "на диске не меняются.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lst = QListWidget()
        for b in items:
            it = QListWidgetItem(f"{b.when:%d.%m.%Y %H:%M:%S} — реплик: {b.lines}, "
                                 f"записано: {b.recorded}")
            it.setData(Qt.UserRole, str(b.path))
            lst.addItem(it)
        lst.setCurrentRow(0)
        lay.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Восстановить")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lst.itemDoubleClicked.connect(lambda *_: dlg.accept())
        lay.addWidget(bb)
        dlg.resize(460, 420)
        if dlg.exec() != QDialog.Accepted or lst.currentItem() is None:
            return
        try:
            state = load_backup_state(lst.currentItem().data(Qt.UserRole))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Резервные копии", f"Не удалось прочитать копию:\n{e}")
            return
        self.flush_history()
        p.restore(state)
        self.chars.reload()
        self.refresh_all()
        bg = p.resolve(p.background_wav)
        self.mix_panel.set_mix(p.mix, bool(bg and bg.exists()))
        self._apply_track_mutes()
        self.mark_dirty("Восстановление из копии")
        self.statusBar().showMessage("Проект восстановлен из копии — отменить: Ctrl+Z", 6000)

    def toggle_actor_window(self, on: bool = True):
        from .actor_window import ActorWindow
        if self.actor_win is not None:
            self.actor_win.close()
            return
        if not on:
            return
        w = ActorWindow(self)
        w.closed.connect(self._actor_closed)
        self.actor_win = w
        self.a_actor.setChecked(True)
        w.place()

    def _actor_closed(self):
        self.actor_win = None
        self.a_actor.setChecked(False)

    def _open_batch(self):
        from .batch_dialog import open_batch
        open_batch(self)

    def diarize_roles(self):
        p = self.project
        if not p:
            return
        from PySide6.QtWidgets import QFormLayout, QRadioButton
        from ..core import diarize
        dlg = QDialog(self)
        dlg.setWindowTitle("Роли по голосам")
        f = QFormLayout(dlg)
        info = QLabel("Реплики группируются по звучанию голоса. Чтобы группы получили имена "
                      "ваших персонажей, заранее отметьте по 2–3 реплики каждого — остальные "
                      "программа определит сама. Новые голоса получат имена «Голос 1», «Голос 2»…\n"
                      "Точнее работает после «Отделить голоса».")
        info.setWordWrap(True)
        f.addRow(info)
        n = QComboBox()
        n.addItem("Определить автоматически", None)
        for k in range(2, 21):
            n.addItem(str(k), k)
        f.addRow("Сколько голосов:", n)
        r1 = QRadioButton("Только репликам без роли")
        r2 = QRadioButton("Всем репликам (перезаписать роли)")
        r1.setChecked(True)
        f.addRow(r1)
        f.addRow(r2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        only, voices = r1.isChecked(), n.currentData()
        model_dir = str(data_dir() / "models" / "wespeaker")

        def work(t: Task):
            q = Project.from_dict(p.to_dict())   # считаем на копии: интерфейс не трогаем
            q.path = p.path
            res = diarize.assign_roles(q, only, voices, model_dir, t.report, t.is_cancelled)
            return q, res

        def done(out):
            q, res = out
            p.characters = q.characters
            for ln, lq in zip(p.lines, q.lines):
                ln.character_id = lq.character_id
            for ch in res.new_characters:
                prepare_new_character(p, ch)
            self.chars.reload()
            self.refresh_all()
            self.mark_dirty("Роли по голосам")
            msg = f"Назначено ролей: {res.assigned}."
            if res.new_characters:
                msg += f"\nНовые персонажи: {', '.join(c.name for c in res.new_characters)} — " \
                       "переименуйте их на вкладке «Персонажи» или объедините с нужными."
            if res.skipped_short:
                msg += f"\nСлишком коротких реплик (меньше 0.6 с) пропущено: {res.skipped_short}."
            if not res.used_model:
                msg += ("\n\nМодель голосов не загрузилась (нет интернета или модуля "
                        "onnxruntime) — использован простой способ, он различает в основном "
                        "мужские, женские и детские голоса. Проверьте результат.")
            QMessageBox.information(self, "Роли по голосам", msg)

        self.run_task(Task("Роли по голосам", work), done)

    def export_stems(self):
        p = self.project
        if not p:
            return
        start = str(Path(p.path).parent) if p.path else ""
        d = QFileDialog.getExistingDirectory(self, "Папка для дорожек", start)
        if not d:
            return
        stem = Path(p.path).stem if p.path else "проект"
        out = Path(d) / f"{stem} — дорожки"
        if out.exists() and any(out.iterdir()) and QMessageBox.question(
                self, "Экспорт по дорожкам",
                f"Папка «{out.name}» уже есть. Перезаписать файлы в ней?") != QMessageBox.Yes:
            return
        from ..core.stems import export_stems

        def work(t: Task):
            return export_stems(p, str(out), t.report, t.is_cancelled)

        def done(files):
            QMessageBox.information(
                self, "Экспорт по дорожкам",
                f"Готово: {len(files)} файлов в папке\n{out}\n\nОткройте «Озвучка.RPP» в Reaper: "
                "фон и микс — целиком, реплики — отдельными клипами на дорожках персонажей.")

        self.run_task(Task("Экспорт по дорожкам", work), done)

    def align_recordings(self, ids: Optional[list[str]] = None, quiet: bool = True):
        """Подогнать сдвиг записей к голосу оригинала (выделенные реплики или ids)."""
        from ..core.align import align_line, reference_path
        p = self.project
        if not p:
            return
        ref = reference_path(p)
        if not ref or not ref.exists():
            if not quiet:
                self.statusBar().showMessage("Нет звука оригинала для подгонки", 5000)
            return
        lines = ([ln for ln in p.lines if ln.id in set(ids)] if ids is not None
                 else [ln for ln in self.selected_lines() if ln.rec_path])
        if ids is None and not lines:
            self.statusBar().showMessage("Выделите реплики с записями", 4000)
            return
        moved, weak = 0, 0
        for ln in lines:
            r = align_line(p, ln)
            if r is None:
                continue
            if r.applied:
                moved += 1
                self.lines_model.refresh_line(ln.id)
            elif r.score < 0.3:
                weak += 1
        if moved:
            self.timeline.canvas.update()
            self.record.refresh_take()
            self.mark_dirty("Подгонка к оригиналу")
        if len(lines) == 1 and lines:
            r_text = ("Запись подогнана к оригиналу" if moved else
                      ("Не удалось уверенно сопоставить с оригиналом — сдвиг не менялся" if weak
                       else "Запись уже совпадает с оригиналом"))
        else:
            r_text = f"Подогнано записей: {moved} из {len(lines)}" + (
                f", не удалось сопоставить: {weak}" if weak else "")
        self.statusBar().showMessage(r_text, 6000)

    def selected_lines(self) -> list[Line]:
        if not self.project:
            return []
        rows = sorted({self.lines_filter.mapToSource(i).row()
                       for i in self.table.selectionModel().selectedRows()})
        return [self.project.lines[r] for r in rows if 0 <= r < len(self.project.lines)]

    def current_line(self) -> Optional[Line]:
        if not self.project:
            return None
        idx = self.table.currentIndex()
        if idx.isValid():
            r = self.lines_filter.mapToSource(idx).row()
            if 0 <= r < len(self.project.lines):
                return self.project.lines[r]
        sel = self.selected_lines()
        return sel[0] if sel else None

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
        self.orig_lbl.setText(f"Оригинал: {ln.orig_text}" if ln and ln.orig_text else "")
        self.orig_lbl.setVisible(bool(ln and ln.orig_text))
        if ln:
            self.timeline.canvas.ensure_visible(ln.start, ln.end)
            if not self.player.is_playing() and not self.record.is_recording() and not self._restoring:
                self.player.seek(ln.start)
        self._update_record_line()
        self.timeline.canvas.update()

    def _table_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        if not self.project:
            return
        sel = self.selected_lines()
        m = QMenu(self)
        src, dst = self.settings.translate_source, self.settings.translate_target
        a = m.addAction(f"Перевести выделенные ({src} → {dst})")
        a.triggered.connect(lambda: text_tools.translate_lines(self, self.selected_lines()))
        a.setEnabled(bool(sel))
        m.addAction(self.a_translate)
        a = m.addAction("Вернуть оригинал")
        a.triggered.connect(lambda: text_tools.restore_original(self))
        a.setEnabled(any(ln.orig_text for ln in sel))
        m.addSeparator()
        m.addAction(self.a_split)
        m.addAction(self.a_merge)
        m.addSeparator()
        a = m.addAction("Удалить")
        a.triggered.connect(self.delete_lines)
        a.setEnabled(bool(sel))
        m.exec(self.table.viewport().mapToGlobal(pos))

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

    def _refresh_chars_progress(self):
        """Счётчики и полоски прогресса ролей: сразу, если вкладка видна, иначе — при открытии."""
        if self.tabs.currentWidget() is self.chars:
            cur = self.chars.current()
            self.chars.reload(cur.id if cur else None)
            self._chars_stale = False
        else:
            self._chars_stale = True

    def _on_tab_changed(self, _i):
        if self.tabs.currentWidget() is self.chars and getattr(self, "_chars_stale", False):
            self._refresh_chars_progress()

    def _on_line_edited(self, line_id: str, label: str = "Правка реплики"):
        self._refresh_chars_progress()
        self.lines_model.refresh_line(line_id)
        self.timeline.canvas.update()
        self.rythmo.update()
        self._update_stats()
        self._update_now_line(self.player.position())
        self.mark_dirty(label)

    def _text_edited(self):
        ln = self.current_line()
        if ln is None:
            return
        ln.text = self.text_edit.toPlainText().strip()
        self._on_line_edited(ln.id, "Правка текста")

    def _note_edited(self):
        ln = self.current_line()
        if ln is not None and ln.note != self.note_edit.text():
            ln.note = self.note_edit.text()
            self.mark_dirty("Заметка")

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
            prepare_new_character(self.project, ch)
            data = ch.id
            self.chars.reload(ch.id)
        for ln in lines:
            ln.character_id = data or None
        self.lines_model.refresh_all()
        self._fill_char_combos()
        self.chars.reload()
        self.timeline.refresh()
        self._update_stats()
        self.mark_dirty("Назначение роли")

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
        self.mark_dirty("Новая реплика")

    # ------------------------------------------------------------ метки
    def add_marker(self, t: float):
        from ..core.models import add_marker
        from .dialogs import ask_text
        if not self.project:
            return
        text = ask_text(self, "Метка", f"Заметка к моменту {fmt_clock(t)}:")
        if text is None:
            return
        add_marker(self.project, t, text or "метка")
        self.timeline.canvas.update()
        self.mark_dirty("Метка")

    def edit_marker(self, marker_id: str):
        p = self.project
        m = next((x for x in p.markers if x["id"] == marker_id), None) if p else None
        if m is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Метка {fmt_clock(m['t'])}")
        lay = QVBoxLayout(dlg)
        ed = QLineEdit(m["text"])
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b_go = bb.addButton("Перейти", QDialogButtonBox.ActionRole)
        b_del = bb.addButton("Удалить", QDialogButtonBox.DestructiveRole)
        result = {"act": ""}
        b_go.clicked.connect(lambda: (result.update(act="go"), dlg.accept()))
        b_del.clicked.connect(lambda: (result.update(act="del"), dlg.accept()))
        bb.accepted.connect(lambda: (result.update(act="ok"), dlg.accept()))
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(420, 100)
        if dlg.exec() != QDialog.Accepted:
            return
        if result["act"] == "del":
            p.markers.remove(m)
            self.mark_dirty("Удаление метки")
        elif result["act"] == "go":
            self.player.seek(m["t"])
        elif ed.text().strip() != m["text"]:
            m["text"] = ed.text().strip()
            self.mark_dirty("Метка")
        self.timeline.canvas.update()

    def goto_marker(self, direction: int):
        p = self.project
        if not p or not p.markers:
            self.statusBar().showMessage("Меток нет: двойной клик по линейке таймлайна или "
                                         "клавиша M — поставить", 4000)
            return
        t = self.player.position()
        ms = sorted(p.markers, key=lambda x: x["t"])
        if direction > 0:
            m = next((x for x in ms if x["t"] > t + 0.05), None)
        else:
            m = next((x for x in reversed(ms) if x["t"] < t - 0.05), None)
        if m is None:
            return
        self.player.seek(m["t"])
        self.timeline.canvas.ensure_visible(m["t"], m["t"])
        self.statusBar().showMessage(f"Метка {fmt_clock(m['t'])}: {m['text']}", 6000)

    def split_current(self):
        from ..core.edit_ops import split_line
        p = self.project
        if not p:
            return
        t = self.player.position()
        ln = self.current_line()
        if ln is None or not (ln.start < t < ln.end):
            ln = next((x for x in p.lines if x.start < t < x.end), None)
        if ln is None:
            self.statusBar().showMessage("Поставьте курсор видео внутрь реплики, которую нужно "
                                         "разрезать", 5000)
            return
        new = split_line(p, ln, t)
        if new is None:
            self.statusBar().showMessage("Слишком близко к краю реплики", 4000)
            return
        self.refresh_all()
        self.select_line(new.id, seek=False)
        self.mark_dirty("Разрезание реплики")
        self.statusBar().showMessage("Реплика разрезана. Ctrl+Z — отменить.", 5000)

    def merge_selected(self):
        from ..core.edit_ops import merge_lines
        p = self.project
        if not p:
            return
        merged, warn = merge_lines(p, self.selected_lines())
        if merged is None:
            self.statusBar().showMessage(warn, 5000)
            return
        self.refresh_all()
        self.select_line(merged.id, seek=False)
        self.mark_dirty("Склейка реплик")
        if warn:
            QMessageBox.information(self, "Склейка реплик", warn)
        else:
            self.statusBar().showMessage("Реплики склеены. Ctrl+Z — отменить.", 5000)

    def delete_lines(self):
        lines = self.selected_lines()
        if not lines or not self.project:
            return
        ids = {ln.id for ln in lines}
        self.project.lines = [ln for ln in self.project.lines if ln.id not in ids]
        self.refresh_all()
        self.mark_dirty(f"Удаление реплик ({len(ids)})")
        self.statusBar().showMessage(f"Удалено реплик: {len(ids)}. Ctrl+Z — вернуть.", 5000)

    def _step_line(self, delta: int):
        """Перейти к соседней реплике (для записи — той же роли)."""
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
        src, _ = mixer.clip_source(self.project, ln)
        if src is None:
            self.statusBar().showMessage("У реплики ещё нет записи — запишите её на вкладке «Запись»", 4000)
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
        self.mark_dirty("Настройки персонажа")

    def _on_chars_list_changed(self):
        self._fill_char_combos()
        self.lines_model.refresh_all()
        self.timeline.refresh()
        self._update_stats()
        self.mark_dirty("Персонажи")

    # ================================================================ задачи
    def run_task(self, task: Task, on_result: Optional[Callable] = None,
                 on_message: Optional[Callable] = None):
        if self.task is not None:
            QMessageBox.information(self, "Подождите", f"Выполняется: {self.task.title}")
            return
        self.flush_history()
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
        # сначала снимаем «занято», затем обрабатываем результат (он может запустить новую задачу)
        task.signals.finished.connect(fin)
        if on_result:
            task.signals.result.connect(lambda r: QTimer.singleShot(0, lambda: on_result(r)))
        if on_message:
            task.signals.message.connect(on_message)
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

        self.player.release_stems()   # Windows не даст перезаписать открытый файл

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
                self.mark_dirty("Режим дубляжа")
            self.mix_panel.set_mix(p.mix, True)
            self._load_waveform()
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
            self.timeline.canvas.preview_range = (rng[0], rng[0] + length)
            self.statusBar().showMessage(
                f"Превью {fmt_clock(rng[0])}–{fmt_clock(rng[0] + length)} готово. "
                f"Звук плеера переключён на озвучку.", 8000)
            if autoplay or seconds is not None:
                self.player.seek(rng[0])
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
        shown = set(mixer.wave_keys(p)) & set(p.mix.muted)
        if shown:
            names = {"original": "оригинал", "vocals": "голоса оригинала", "background": "фон"}
            if QMessageBox.question(
                    self, "Экспорт",
                    "Заглушены дорожки: " + ", ".join(names[k] for k in sorted(shown)) +
                    ".\nВ готовом видео их не будет. Продолжить?") != QMessageBox.Yes:
                return
        missing = [ln for ln in p.lines if line_status(p, ln) == "none"]
        if missing and QMessageBox.question(
                self, "Экспорт",
                f"У {len(missing)} реплик ещё нет записи.\nВсё равно экспортировать?") != QMessageBox.Yes:
            return
        default = str(Path(p.video_path).with_name(Path(p.video_path).stem + ".voiceover.mkv"))
        dlg = ExportDialog(default, self)
        dlg.loudness.setCurrentIndex(max(0, dlg.loudness.findData(self.settings.export_loudness)))
        dlg.logo.setText(self.settings.export_logo)
        dlg.logo_pos.setCurrentIndex(max(0, dlg.logo_pos.findData(self.settings.export_logo_pos)))
        dlg.intro.setText(self.settings.export_intro)
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        self.settings.export_loudness = opts.loudness
        self.settings.save()
        out = dlg.out.text().strip()
        kind = dlg.kind.currentData()
        if Path(out).resolve() == Path(p.video_path).resolve():
            QMessageBox.warning(self, "Экспорт", "Нельзя перезаписать исходное видео.")
            return
        if Path(out).exists() and QMessageBox.question(
                self, "Экспорт", f"Файл уже существует:\n{out}\n\nЗаменить его?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        burn = dlg.burn.isChecked() and kind == "video"
        self.settings.export_logo = opts.logo
        self.settings.export_logo_pos = opts.logo_pos
        self.settings.export_intro = opts.intro
        self.settings.save()

        def work(t: Task):
            from ..core.export import export_project, write_burn_subs
            if burn:
                opts.burn_subs = write_burn_subs(p, str(p.project_dir / "burn.ass"))
            return export_project(p, out, opts, kind, t.report, t.is_cancelled)

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
        lbl.setTextFormat(Qt.PlainText)
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

    # ================================================================ прочее
    def open_settings(self):
        lang = self.settings.spell_lang
        SettingsDialog(self.settings, self).exec()
        ffmpeg.set_ffmpeg_path(self.settings.ffmpeg_path)
        if self.settings.spell_lang != lang:
            self.spell.set_language(self.settings.spell_lang)
            self._fill_char_combos()

    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> {__version__}<br>"
                          "Многоголосая озвучка фильмов, сериалов, мультфильмов и аниме.<br><br>"
                          "Распознавание: Whisper (faster-whisper). Отделение голосов: Demucs. "
                          "Голоса: WeSpeaker. Орфография: словари LibreOffice (spylls). "
                          "Видео/звук: FFmpeg. Интерфейс: Qt (PySide6).")

    def help(self):
        QMessageBox.information(self, "Как пользоваться", HELP_TEXT)

    def eventFilter(self, obj, ev):
        # Горячие клавиши, которые не должны мешать вводу текста
        try:
            if ev.type() == QEvent.KeyPress and self.isActiveWindow() and self.project:
                return self._hotkey(ev) or super().eventFilter(obj, ev)
            return super().eventFilter(obj, ev)
        except RuntimeError:   # окно уже уничтожено (завершение программы)
            return False

    def _hotkey(self, ev) -> bool:
        if True:
            fw = QApplication.focusWidget()
            typing = isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)) or \
                (isinstance(fw, QComboBox) and fw.isEditable()) or \
                (self.table.state() == QAbstractItemView.EditingState)
            if ev.isAutoRepeat() and ev.key() not in (Qt.Key_Left, Qt.Key_Right):
                return False
            mods = ev.modifiers() & ~Qt.KeypadModifier
            if typing and not mods & (Qt.ControlModifier | Qt.AltModifier):
                return False
            if ev.key() in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
                return False
            seq = QKeySequence(mods.value | ev.key()).toString().lower()
            action = self._single_keys.get(seq)
            if action is None:
                return False
            recording = self.record.is_recording() or self.record.loop_pending
            if action == "play":
                if recording:
                    self.record.stop_recording(manual=True)
                else:
                    self.player.toggle()
            elif action == "record":
                if recording:
                    self.record.stop_recording(manual=True)
                elif self.tabs.currentWidget() is self.record:
                    self.record.start_recording()
                else:
                    return False
            elif action == "play_take":
                self.play_selected_clip()
            elif action == "new_line":
                self.add_line()
            elif action == "delete":
                if fw is not self.table:
                    return False
                self.delete_lines()
            elif action in ("back", "fwd"):
                if fw is self.table and not mods:
                    return False
                self.player.seek(self.player.position() + (-2 if action == "back" else 2))
            elif action == "marker":
                self.add_marker(self.player.position())
            elif action in ("prev_line", "next_line"):
                if recording:
                    return False
                self.record.requestLine.emit(-1 if action == "prev_line" else 1)
            return True
        return False

    # ------------------------------------------------------------ горячие клавиши
    def apply_hotkeys(self):
        from ..core.hotkeys import SINGLE, action_key
        hk = self.settings.hotkeys
        if not hasattr(self, "_default_shortcuts"):
            self._default_shortcuts = {action_key(a.text()): a.shortcut().toString()
                                       for a in self.findChildren(QAction) if a.text()}
        for a in self.findChildren(QAction):
            k = action_key(a.text())
            if k in hk:
                a.setShortcut(QKeySequence(hk[k]))
            elif k in self._default_shortcuts:
                a.setShortcut(QKeySequence(self._default_shortcuts[k]))
        self._single_keys = {}
        for k, (_t, default) in SINGLE.items():
            seq = hk.get(k, default)
            if seq:
                self._single_keys[QKeySequence(seq).toString().lower()] = k

    def edit_hotkeys(self):
        from .hotkeys_dialog import HotkeysDialog
        if HotkeysDialog(self).exec():
            self.settings.save()
            self.apply_hotkeys()

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
        if self.actor_win is not None:
            self.actor_win.close()
        self.record.shutdown()
        self.settings.save()
        QApplication.instance().removeEventFilter(self)
        e.accept()


HELP_TEXT = """Порядок работы:

1. Новый проект: выберите видео, аудиодорожку и (по желанию) субтитры.
   Тип озвучки: закадровая (оригинал тихо) или дубляж (голоса удаляются).

2. Текст реплик: импорт субтитров (SRT/ASS/VTT) или «Распознать» (Whisper).
   При необходимости — «Перевести». Персонажи из субтитров создаются сами;
   после распознавания роли назначаются вручную: выделите реплики → «Роль».

3. Вкладка «Персонажи»: укажите актёра каждой роли. «Сохранить состав…» —
   чтобы в следующей серии подставить актёров одним нажатием.

4. Вкладка «Запись»: выберите реплику и нажмите R — видео пойдёт с преролла,
   говорите, когда появится «Говорите». Запись остановится сама.

5. Для дубляжа: «Отделить голоса» (Demucs).

6. F6 — превью минуты от курсора вместе с видео. Ctrl+E — экспорт.

Клавиши: Ctrl+Z / Ctrl+Y — отменить / повторить, Пробел — пуск/пауза,
←/→ — перемотка, P — прослушать запись реплики, N — новая реплика,
R — запись, Del — удалить, Ctrl+T — сменить тему,
Ctrl+колесо на таймлайне — масштаб, двойной клик по блоку — проиграть реплику.
"""
