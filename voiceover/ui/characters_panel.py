"""Панель персонажей: список ролей, актёр, цвет и громкость для каждой, библиотека составов."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from ..core.models import Character, Project
from . import theme


def color_icon(color: str) -> QIcon:
    """Квадратный ключ цвета роли (как на таймлайне)."""
    from PySide6.QtGui import QPainter
    pm = QPixmap(24, 24)
    pm.fill(Qt.transparent)
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


def prepare_new_character(project: Project, c: Character) -> None:
    """Новый персонаж: если у проекта есть состав и в нём есть этот персонаж —
    берём из состава актёра, цвет и громкость."""
    if project.cast_name:
        from ..core.cast import apply_cast
        apply_cast(project, project.cast_name, only=[c])


class CharacterRowDelegate(QStyledItemDelegate):
    """Строка списка ролей: ключ цвета, имя, число реплик и записанные — в колонках."""

    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize
        return QSize(option.rect.width(), 46)

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
        p.drawText(r.adjusted(16, 0, -250, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(name, Qt.ElideRight, max(20, r.width() - 270)))
        p.setFont(theme.body_font(theme.TEXT_SM, tabular=True))
        p.setPen(QColor(th.muted))
        p.drawText(QRectF(r.right() - 245, r.top(), 110, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                   index.data(Qt.UserRole + 2) or "")
        p.drawText(QRectF(r.right() - 125, r.top(), 115, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(index.data(Qt.UserRole + 3) or "", Qt.ElideRight, 115))
        # полоска прогресса записи роли
        frac = float(index.data(Qt.UserRole + 4) or 0.0)
        track = QRectF(r.left() + 16, r.bottom() - 7, max(10.0, r.width() - 26), 3)
        p.fillRect(track, QColor(th.rule))
        if frac > 0:
            fill = QRectF(track)
            fill.setWidth(track.width() * min(1.0, frac))
            p.fillRect(fill, QColor(th.success) if frac >= 1 else QColor(index.data(Qt.UserRole + 1)))
        p.restore()


class CharactersPanel(QWidget):
    changed = Signal()                 # изменились настройки персонажа
    listChanged = Signal()             # добавлен/удалён/переименован
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
        top = QHBoxLayout()
        for b in (btn_add, btn_del, btn_merge):
            top.addWidget(b)
        top.addStretch(1)
        # составы — одна кнопка с меню: сохраняют и загружают их раз в серию
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu, QToolButton
        self.btn_cast_save = QAction(theme.icon("save"), "Сохранить состав…", self)
        self.btn_cast_save.setToolTip("Сохранить актёров и цвета персонажей в библиотеку — "
                                      "для следующих серий")
        self.btn_cast_load = QAction(theme.icon("users"), "Загрузить состав…", self)
        self.btn_cast_load.setToolTip("Применить актёров и цвета из библиотеки к персонажам "
                                      "с теми же именами")
        cast_menu = QMenu(self)
        cast_menu.addAction(self.btn_cast_load)
        cast_menu.addAction(self.btn_cast_save)
        self.btn_cast = QToolButton()
        self.btn_cast.setObjectName("menuButton")
        self.btn_cast.setText("Состав")
        self.btn_cast.setMenu(cast_menu)
        self.btn_cast.setPopupMode(QToolButton.InstantPopup)
        self.btn_cast.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_cast.setToolTip("Актёры и цвета ролей между сериями")
        self.cast_lbl = QLabel()
        self.cast_lbl.setObjectName("muted")
        self.cast_lbl.setTextFormat(Qt.PlainText)
        top.addWidget(self.cast_lbl)
        top.addWidget(self.btn_cast)

        self.box = QGroupBox("")
        f = QFormLayout(self.box)
        self.name = QLineEdit()
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(48, 24)
        self.actor = QLineEdit()
        self.actor.setPlaceholderText("кто озвучивает (необязательно)")
        self.volume = QDoubleSpinBox()
        self.volume.setRange(-30, 20)
        self.volume.setSingleStep(0.5)
        self.volume.setSuffix(" дБ")
        self.volume.setToolTip("Громкость записей этого персонажа при сведении")
        from ..core.fx import EFFECTS
        self.effect = QComboBox()
        for k, (title, _f) in EFFECTS.items():
            self.effect.addItem(title, k)
        self.effect.setToolTip("Обработка голоса роли при сведении: рация, телефон, эхо зала…")
        self.compress = QCheckBox("Компрессор (ровнее громкость внутри фразы)")
        self.match_eq = QCheckBox("Тембр как в оригинале")
        self.match_eq.setToolTip("Подогнать окраску записи (микрофон, комната) под звук "
                                 "оригинала в этой сцене, не больше ±6 дБ по полосам")
        self.btn_lines = QPushButton(theme.icon("list"), " Показать в таблице")
        self.count_lbl = QLabel()

        f.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        for w in (self.name, self.actor):
            w.setMinimumWidth(280)
        self.effect.setMinimumWidth(220)
        self.volume.setMinimumWidth(110)
        def section(text: str):
            h = QLabel(text)
            h.setObjectName("itemTitle")
            f.addRow(h)

        section("Роль")
        f.addRow("Имя:", self.name)
        f.addRow("Цвет:", self.color_btn)
        f.addRow("Актёр:", self.actor)
        lines_row = QHBoxLayout()
        lines_row.addWidget(self.count_lbl)
        lines_row.addWidget(self.btn_lines)
        lines_row.addStretch(1)
        f.addRow("Реплики:", lines_row)
        section("Голос в сведении")
        f.addRow("Громкость:", self.volume)
        f.addRow("Эффект:", self.effect)
        f.addRow(self.compress)
        f.addRow(self.match_eq)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.box)

        btn_add.clicked.connect(self.add_character)
        btn_del.clicked.connect(self.delete_character)
        btn_merge.clicked.connect(self.merge_character)
        self.btn_cast_save.triggered.connect(self.save_cast)
        self.btn_cast_load.triggered.connect(self.load_cast)
        self.list.currentRowChanged.connect(self._load_current)
        self.color_btn.clicked.connect(self._pick_color)
        self.name.editingFinished.connect(self._save)
        self.actor.editingFinished.connect(self._save)
        self.volume.valueChanged.connect(self._save)
        self.effect.currentIndexChanged.connect(self._save)
        self.compress.toggled.connect(self._save)
        self.match_eq.toggled.connect(self._save)
        self.btn_lines.clicked.connect(
            lambda: self.current() and self.filterRequested.emit(self.current().id))
        self.box.setEnabled(False)

    # ---------------------------------------------------------- данные
    def set_project(self, p: Optional[Project]) -> None:
        self.project = p
        self.reload()

    def reload(self, select_id: Optional[str] = None) -> None:
        self._update_cast_label()
        cur = select_id or (self.current().id if self.current() else None)
        self.list.blockSignals(True)
        self.list.clear()
        if self.project:
            counts: dict[str, int] = {}
            recorded: dict[str, int] = {}
            for ln in self.project.lines:
                if ln.character_id:
                    counts[ln.character_id] = counts.get(ln.character_id, 0) + 1
                    if ln.rec_path:
                        recorded[ln.character_id] = recorded.get(ln.character_id, 0) + 1
            for c in self.project.characters:
                n = counts.get(c.id, 0)
                it = QListWidgetItem(c.name)
                it.setData(Qt.UserRole + 1, c.color)
                it.setData(Qt.UserRole + 2, f"записано {recorded.get(c.id, 0)} из {n}")
                it.setData(Qt.UserRole + 3, c.actor)   # пусто — ничего не пишем
                it.setData(Qt.UserRole + 4, recorded.get(c.id, 0) / n if n else 0.0)
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

    def _load_current(self, *_):
        c = self.current()
        self.box.setEnabled(c is not None)
        if c is None:
            return
        self._loading = True
        self.name.setText(c.name)
        self.color_btn.setStyleSheet(f"background:{c.color}; border:0")
        self.actor.setText(c.actor)
        self.volume.setValue(c.volume_db)
        self.effect.setCurrentIndex(max(0, self.effect.findData(c.effect)))
        self.compress.setChecked(c.compress)
        self.match_eq.setChecked(c.match_eq)
        n = sum(1 for ln in self.project.lines if ln.character_id == c.id) if self.project else 0
        self.count_lbl.setText(lines_word(n))
        self._loading = False

    def _save(self, *_):
        c = self.current()
        if self._loading or c is None:
            return
        old_name, old_actor = c.name, c.actor
        name = self.name.text().strip()
        if name and name != c.name:
            if self.project and self.project.character_by_name(name) not in (None, c):
                QMessageBox.warning(self, "Персонаж", f"Персонаж «{name}» уже есть.")
                self.name.setText(c.name)
            else:
                c.name = name
        c.actor = self.actor.text().strip()
        c.volume_db = self.volume.value()
        c.effect = self.effect.currentData() or ""
        c.compress = self.compress.isChecked()
        c.match_eq = self.match_eq.isChecked()
        self.changed.emit()
        if c.name != old_name or c.actor != old_actor:
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
            prepare_new_character(self.project, c)
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

    def _update_cast_label(self):
        name = self.project.cast_name if self.project else ""
        self.cast_lbl.setText(f"Состав: {name}" if name else "Состав не выбран")

    def save_cast(self):
        from ..core import cast
        p = self.project
        if not p or not p.characters:
            return
        names = cast.list_casts()
        default = p.cast_name or (Path(p.video_path).parent.name if p.video_path else "")
        name, ok = QInputDialog.getItem(
            self, "Сохранить состав",
            "Название состава (например, название сериала).\n"
            "Если состав уже есть, он дополнится персонажами этой серии:",
            ([default] if default and default not in names else []) + names, 0, True)
        name = name.strip() if ok else ""
        if not name:
            return
        n = cast.save_cast(name, p.characters)
        p.cast_name = name
        self._update_cast_label()
        self.changed.emit()
        QMessageBox.information(self, "Состав", f"Состав «{name}» сохранён: персонажей — {n}.\n\n"
                                "В следующей серии нажмите «Загрузить состав…».")

    def load_cast(self):
        from ..core import cast
        p = self.project
        if not p:
            return
        names = cast.list_casts()
        if not names:
            QMessageBox.information(self, "Состав", "В библиотеке пока нет составов. Укажите актёров "
                                    "и нажмите «Сохранить состав…».")
            return
        start = names.index(p.cast_name) if p.cast_name in names else 0
        name, ok = QInputDialog.getItem(self, "Загрузить состав", "Состав:", names, start, False)
        if not ok:
            return
        have = {c.name.strip().lower() for c in p.characters}
        missing = [e["name"] for e in cast.get_cast(name) if e["name"].strip().lower() not in have]
        add = False
        if missing:
            r = QMessageBox.question(
                self, "Загрузить состав",
                f"В составе есть персонажи, которых нет в проекте ({len(missing)}): "
                f"{', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}.\n\nДобавить их?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            add = r == QMessageBox.Yes
        upd, added = cast.apply_cast(p, name, add_missing=add)
        self._update_cast_label()
        self.reload()
        self.changed.emit()
        self.listChanged.emit()
        QMessageBox.information(self, "Состав", f"Состав «{name}»: настройки применены к {upd} "
                                f"персонажам, добавлено {added}.\nНовые персонажи из субтитров "
                                "получат актёров этого состава автоматически.")
