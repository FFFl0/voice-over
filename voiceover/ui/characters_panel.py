"""Панель персонажей: список ролей и настройки голоса для каждой."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QStyledItemDelegate, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..core.models import Character, Engine, LineSource, Project
from ..tts import get_engine, list_engines
from . import theme

PREVIEW_TEXT = "Привет! Так звучит мой голос в этой озвучке."


def color_icon(color: str) -> QIcon:
    """Квадратный ключ цвета роли (как на таймлайне)."""
    from PySide6.QtCore import Qt as _Qt
    pm = QPixmap(24, 24)
    pm.fill(_Qt.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pm)
    p.fillRect(6, 6, 12, 12, QColor(color))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def lines_word(n: int) -> str:
    """«1 реплика», «3 реплики», «5 реплик»."""
    if n % 10 == 1 and n % 100 != 11:
        w = "реплика"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        w = "реплики"
    else:
        w = "реплик"
    return f"{n} {w}"


class CharacterRowDelegate(QStyledItemDelegate):
    """Строка списка ролей: ключ цвета, имя, число реплик и способ озвучки — в колонках."""

    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize
        return QSize(option.rect.width(), 40)

    def paint(self, p, option, index):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QStyle
        self.initStyleOption(option, index)
        name = option.text
        option.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, option, p, option.widget)
        th = theme.current()
        r = option.rect
        p.save()
        p.fillRect(QRectF(r.left(), r.top(), 4, r.height()), QColor(index.data(Qt.UserRole + 1)))
        p.setFont(theme.body_font(theme.TEXT_MD, QFont.Medium))
        p.setPen(QColor(th.ink))
        p.drawText(r.adjusted(16, 0, -230, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, max(20, r.width() - 250)))
        p.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        p.setPen(QColor(th.muted))
        p.drawText(QRectF(r.right() - 225, r.top(), 90, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                   index.data(Qt.UserRole + 2) or "")
        p.drawText(QRectF(r.right() - 125, r.top(), 115, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   index.data(Qt.UserRole + 3) or "")
        p.restore()


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
        self.list.setItemDelegate(CharacterRowDelegate(self.list))
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
        self.color_btn.setFixedSize(48, 24)
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
        self.btn_preview = QPushButton(theme.icon("play"), " Прослушать голос")
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
                if c.source == LineSource.RECORDING:
                    kind = f"запись, {c.actor}" if c.actor else "живая запись"
                else:
                    kind = {"edge": "Edge TTS", "silero": "Silero"}.get(c.engine.value, c.engine.value)
                it = QListWidgetItem(c.name)
                it.setData(Qt.UserRole + 1, c.color)
                it.setData(Qt.UserRole + 2, lines_word(counts.get(c.id, 0)))
                it.setData(Qt.UserRole + 3, kind)
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
        self.color_btn.setStyleSheet(f"background:{c.color}; border:0")
        self.source.setCurrentIndex(self.source.findData(c.source.value))
        self.actor.setText(c.actor)
        self.engine.setCurrentIndex(max(0, self.engine.findData(c.engine.value)))
        self._fill_voices(c.engine.value, c.voice)
        self.rate.setValue(c.rate)
        self.pitch.setValue(c.pitch)
        self.volume.setValue(c.volume_db)
        n = sum(1 for ln in self.project.lines if ln.character_id == c.id) if self.project else 0
        self.count_lbl.setText(lines_word(n))
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
            self.color_btn.setStyleSheet(f"background:{c.color}; border:0")
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
        engines = [e for e in list_engines()]
        titles = [e.title + ("" if e.available()[0] else " — не установлен") for e in engines]
        cur = preferred_engine(self.project)
        start = next((i for i, e in enumerate(engines) if e.id == cur), 0)
        title, ok = QInputDialog.getItem(
            self, "Раздать голоса",
            "Назначить всем AI-персонажам разные голоса движка\n"
            "(текущие настройки голосов будут заменены):", titles, start, False)
        if not ok:
            return
        engine = engines[titles.index(title)]
        avail, why = engine.available()
        if not avail:
            QMessageBox.warning(self, "Раздать голоса", why)
            return
        for i, c in enumerate(self.project.characters):
            if c.source == LineSource.TTS:
                auto_assign_voice(self.project, c, i, engine.id)
        self.reload()
        self.changed.emit()
        self.listChanged.emit()


FEMALE_HINTS = ("а", "я")  # окончания русских женских имён — очень грубая эвристика


def preferred_engine(project: Project, exclude: Optional[Character] = None) -> str:
    """Движок для нового персонажа: тот, которым озвучено большинство AI-персонажей проекта
    (если пользователь перевёл всех на Piper, новые роли тоже получат Piper)."""
    counts: dict[str, int] = {}
    for x in project.characters:
        if x is not exclude and x.source == LineSource.TTS:
            counts[x.engine.value] = counts.get(x.engine.value, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    for eid in ("edge", "piper", "silero"):
        if get_engine(eid).available()[0]:
            return eid
    return "edge"


def auto_assign_voice(project: Project, c: Character, index: Optional[int] = None,
                      engine: Optional[str] = None) -> None:
    """Подобрать голос так, чтобы персонажи звучали по-разному: перебираем голоса
    и добавляем вариации высоты/темпа, когда голоса заканчиваются."""
    if index is None:
        index = project.characters.index(c)
    engine = engine or preferred_engine(project, exclude=c)
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
