"""Панель персонажей: список ролей и настройки голоса для каждой."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..core.models import Character, Engine, LineSource, Project
from ..tts import get_engine, list_engines
from . import theme

PREVIEW_TEXT = "Привет! Так звучит мой голос в этой озвучке."


def color_icon(color: str) -> QIcon:
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QPainter
    pm = QPixmap(28, 28)
    pm.fill(_Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(_Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(4, 4, 20, 20)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


class CharactersPanel(QWidget):
    changed = Signal()                 # изменились настройки персонажа
    listChanged = Signal()             # добавлен/удалён/переименован
    previewRequested = Signal(object, str)  # Character, текст
    filterRequested = Signal(str)      # показать реплики персонажа

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self._loading = False

        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        btn_add = QPushButton(theme.icon("plus"), " Добавить")
        btn_del = QPushButton(theme.icon("trash"), " Удалить")
        btn_merge = QPushButton(theme.icon("users"), " Объединить…")
        btn_merge.setToolTip("Перенести все реплики выбранного персонажа в другого и удалить его")
        btn_auto = QPushButton(theme.icon("synth"), " Раздать голоса")
        btn_auto.setToolTip("Автоматически назначить разные голоса всем персонажам")
        top = QHBoxLayout()
        for b in (btn_add, btn_del, btn_merge, btn_auto):
            top.addWidget(b)

        self.box = QGroupBox("Настройки персонажа")
        f = QFormLayout(self.box)
        self.name = QLineEdit()
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(60, 24)
        self.source = QComboBox()
        self.source.addItem("Синтез речи (AI)", LineSource.TTS.value)
        self.source.addItem("Живая запись (актёр)", LineSource.RECORDING.value)
        self.actor = QLineEdit()
        self.actor.setPlaceholderText("кто озвучивает (необязательно)")
        self.engine = QComboBox()
        for e in list_engines():
            ok, why = e.available()
            self.engine.addItem(e.title + ("" if ok else " — недоступен"), e.id)
            if not ok:
                self.engine.setItemData(self.engine.count() - 1, why, Qt.ToolTipRole)
        self.voice = QComboBox()
        self.voice.setEditable(True)
        self.voice.setMaxVisibleItems(20)
        # Клик по полю открывает список голосов (ID можно и ввести вручную)
        self.voice.lineEdit().installEventFilter(self)
        self._voice_click_opens = True
        self.voice.setToolTip("Нажмите, чтобы выбрать голос из списка. ID голоса Edge TTS "
                              "можно также вписать вручную (правый клик → вставить или Ctrl+V)")
        self.rate = QSpinBox()
        self.rate.setRange(-50, 100)
        self.rate.setSuffix(" %")
        self.pitch = QSpinBox()
        self.pitch.setRange(-50, 50)
        self.pitch.setSuffix(" Гц")
        self.pitch.setToolTip("Сдвиг высоты голоса. Помогает сделать из одного голоса "
                              "разных персонажей (детский, басовитый)")
        self.volume = QDoubleSpinBox()
        self.volume.setRange(-30, 20)
        self.volume.setSingleStep(0.5)
        self.volume.setSuffix(" дБ")
        self.preview_text = QLineEdit(PREVIEW_TEXT)
        self.btn_preview = QPushButton(theme.icon("play", "#ffffff"), " Прослушать голос")
        self.btn_preview.setProperty("primary", True)
        self.btn_lines = QPushButton(theme.icon("list"), " Показать реплики")
        self.count_lbl = QLabel()

        f.addRow("Имя:", self.name)
        f.addRow("Цвет:", self.color_btn)
        f.addRow("Озвучка:", self.source)
        f.addRow("Актёр:", self.actor)
        f.addRow("Движок:", self.engine)
        f.addRow("Голос:", self.voice)
        f.addRow("Темп:", self.rate)
        f.addRow("Высота:", self.pitch)
        f.addRow("Громкость:", self.volume)
        f.addRow("Фраза:", self.preview_text)
        row = QHBoxLayout()
        row.addWidget(self.btn_preview)
        row.addWidget(self.btn_lines)
        f.addRow(row)
        f.addRow(self.count_lbl)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.box)

        btn_add.clicked.connect(self.add_character)
        btn_del.clicked.connect(self.delete_character)
        btn_merge.clicked.connect(self.merge_character)
        btn_auto.clicked.connect(self.auto_voices)
        self.list.currentRowChanged.connect(self._load_current)
        self.color_btn.clicked.connect(self._pick_color)
        self.name.editingFinished.connect(self._save)
        self.actor.editingFinished.connect(self._save)
        self.source.currentIndexChanged.connect(self._save)
        self.engine.currentIndexChanged.connect(self._engine_changed)
        self.voice.currentIndexChanged.connect(self._save)
        self.voice.lineEdit().editingFinished.connect(self._save)
        for w in (self.rate, self.pitch, self.volume):
            w.valueChanged.connect(self._save)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_lines.clicked.connect(
            lambda: self.current() and self.filterRequested.emit(self.current().id))
        self.box.setEnabled(False)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if (obj is self.voice.lineEdit() and ev.type() == QEvent.MouseButtonPress
                and self.voice.isEnabled() and not self.voice.view().isVisible()):
            self.voice.showPopup()
            return True
        return super().eventFilter(obj, ev)

    # ---------------------------------------------------------- данные
    def set_project(self, p: Optional[Project]) -> None:
        self.project = p
        self.reload()

    def reload(self, select_id: Optional[str] = None) -> None:
        cur = select_id or (self.current().id if self.current() else None)
        self.list.blockSignals(True)
        self.list.clear()
        if self.project:
            counts: dict[str, int] = {}
            for ln in self.project.lines:
                if ln.character_id:
                    counts[ln.character_id] = counts.get(ln.character_id, 0) + 1
            for c in self.project.characters:
                kind = "живая запись" if c.source == LineSource.RECORDING else c.engine.value.capitalize()
                if c.source == LineSource.RECORDING and c.actor:
                    kind += f" · {c.actor}"
                it = QListWidgetItem(color_icon(c.color),
                                     f"{c.name}   ·  {counts.get(c.id, 0)} реплик  ·  {kind}")
                it.setData(Qt.UserRole, c.id)
                self.list.addItem(it)
        self.list.blockSignals(False)
        row = 0
        if cur:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == cur:
                    row = i
        if self.list.count():
            self.list.setCurrentRow(row)
        self._load_current()

    def current(self) -> Optional[Character]:
        it = self.list.currentItem()
        if not it or not self.project:
            return None
        return self.project.character(it.data(Qt.UserRole))

    def select(self, cid: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == cid:
                self.list.setCurrentRow(i)
                return

    def _fill_voices(self, engine_id: str, current: str) -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        try:
            for v in get_engine(engine_id).voices():
                self.voice.addItem(v.label, v.id)
        except Exception:
            pass
        i = self.voice.findData(current)
        if i >= 0:
            self.voice.setCurrentIndex(i)
        else:
            self.voice.setEditText(current)
        self.voice.blockSignals(False)

    def _load_current(self, *_):
        c = self.current()
        self.box.setEnabled(c is not None)
        if c is None:
            return
        self._loading = True
        self.name.setText(c.name)
        self.color_btn.setStyleSheet(f"background:{c.color}; border-radius:6px; border:none")
        self.source.setCurrentIndex(self.source.findData(c.source.value))
        self.actor.setText(c.actor)
        self.engine.setCurrentIndex(max(0, self.engine.findData(c.engine.value)))
        self._fill_voices(c.engine.value, c.voice)
        self.rate.setValue(c.rate)
        self.pitch.setValue(c.pitch)
        self.volume.setValue(c.volume_db)
        n = sum(1 for ln in self.project.lines if ln.character_id == c.id) if self.project else 0
        self.count_lbl.setText(f"Реплик: {n}")
        self._update_enabled()
        self._loading = False

    def _update_enabled(self):
        tts = self.source.currentData() == LineSource.TTS.value
        for w in (self.engine, self.voice, self.rate, self.pitch, self.btn_preview, self.preview_text):
            w.setEnabled(tts)

    def _engine_changed(self, *_):
        if self._loading:
            return
        eid = self.engine.currentData()
        voices = get_engine(eid).voices()
        self._fill_voices(eid, voices[0].id if voices else "")
        self._save()

    def _save(self, *_):
        c = self.current()
        if self._loading or c is None:
            return
        old_name, old_src = c.name, c.source
        name = self.name.text().strip()
        if name and name != c.name:
            if self.project and self.project.character_by_name(name) not in (None, c):
                QMessageBox.warning(self, "Персонаж", f"Персонаж «{name}» уже есть.")
                self.name.setText(c.name)
            else:
                c.name = name
        c.source = LineSource(self.source.currentData())
        c.actor = self.actor.text().strip()
        c.engine = Engine(self.engine.currentData())
        vid = self.voice.currentData()
        text = self.voice.currentText()
        if vid is None or self.voice.itemText(self.voice.currentIndex()) != text:
            vid = text.strip()
        c.voice = vid or c.voice
        c.rate = self.rate.value()
        c.pitch = self.pitch.value()
        c.volume_db = self.volume.value()
        self._update_enabled()
        self.changed.emit()
        if c.name != old_name or c.source != old_src:
            self.reload(c.id)
            self.listChanged.emit()

    def _pick_color(self):
        c = self.current()
        if not c:
            return
        col = QColorDialog.getColor(QColor(c.color), self, "Цвет персонажа")
        if col.isValid():
            c.color = col.name()
            self.color_btn.setStyleSheet(f"background:{c.color}; border-radius:6px; border:none")
            self.reload(c.id)
            self.listChanged.emit()

    def _preview(self):
        c = self.current()
        if c:
            self.previewRequested.emit(c, self.preview_text.text().strip() or PREVIEW_TEXT)

    # ---------------------------------------------------------- действия
    def add_character(self):
        if not self.project:
            return
        name, ok = QInputDialog.getText(self, "Новый персонаж", "Имя:")
        if ok and name.strip():
            if self.project.character_by_name(name):
                QMessageBox.warning(self, "Персонаж", "Такой персонаж уже есть.")
                return
            c = self.project.add_character(name.strip())
            auto_assign_voice(self.project, c)
            self.reload(c.id)
            self.listChanged.emit()

    def delete_character(self):
        c = self.current()
        if not c or not self.project:
            return
        n = sum(1 for ln in self.project.lines if ln.character_id == c.id)
        if QMessageBox.question(
                self, "Удалить персонажа",
                f"Удалить «{c.name}»? Его реплики ({n}) останутся без роли.") != QMessageBox.Yes:
            return
        self.project.remove_character(c.id)
        self.reload()
        self.listChanged.emit()

    def merge_character(self):
        c = self.current()
        if not c or not self.project or len(self.project.characters) < 2:
            return
        others = [x for x in self.project.characters if x.id != c.id]
        name, ok = QInputDialog.getItem(self, "Объединить",
                                        f"Перенести реплики «{c.name}» в:",
                                        [x.name for x in others], 0, False)
        if not ok:
            return
        target = self.project.character_by_name(name)
        for ln in self.project.lines:
            if ln.character_id == c.id:
                ln.character_id = target.id
        self.project.remove_character(c.id)
        self.reload(target.id)
        self.listChanged.emit()

    def auto_voices(self):
        if not self.project:
            return
        if QMessageBox.question(self, "Раздать голоса",
                                "Назначить всем AI-персонажам разные голоса автоматически?\n"
                                "Текущие настройки голосов будут заменены.") != QMessageBox.Yes:
            return
        for i, c in enumerate(self.project.characters):
            if c.source == LineSource.TTS:
                auto_assign_voice(self.project, c, i)
        self.reload()
        self.changed.emit()
        self.listChanged.emit()


FEMALE_HINTS = ("а", "я")  # окончания русских женских имён — очень грубая эвристика


def auto_assign_voice(project: Project, c: Character, index: Optional[int] = None) -> None:
    """Подобрать голос так, чтобы персонажи звучали по-разному: перебираем голоса
    и добавляем вариации высоты/темпа, когда голоса заканчиваются."""
    if index is None:
        index = project.characters.index(c)
    ok, _ = get_engine("edge").available()
    engine = "edge" if ok else "silero"
    voices = get_engine(engine).voices()
    name = c.name.strip().lower()
    want_f = name.endswith(FEMALE_HINTS)
    pool = [v for v in voices if v.gender == ("F" if want_f else "M")] or voices
    same = [x for x in project.characters[:index] if x.engine.value == engine]
    used = sum(1 for x in same if any(x.voice == v.id for v in pool))
    v = pool[used % len(pool)]
    round_ = used // len(pool)
    variations = [(0, 0), (0, -8), (5, 8), (-5, -15), (10, 15), (-10, 0)]
    rate, pitch = variations[round_ % len(variations)]
    c.engine = Engine(engine)
    c.voice = v.id
    c.rate = rate
    c.pitch = pitch
