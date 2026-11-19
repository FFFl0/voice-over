"""Таблица реплик: модель, фильтр и делегат выбора персонажа."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
                            Signal)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from ..core.models import Line, Project, line_status
from ..core.subtitles import fmt_clock, parse_ts

COLS = ["№", "Начало", "Конец", "Персонаж", "Текст", "Статус"]
C_NUM, C_START, C_END, C_CHAR, C_TEXT, C_STATUS = range(6)

STATUS_TEXT = {
    "ok": ("✔ синтез", "#2e7d32"),
    "rec": ("● запись", "#1565c0"),
    "none": ("— нет звука", "#c62828"),
    "stale": ("↻ устарела", "#ef6c00"),
    "nochar": ("? нет роли", "#757575"),
    "off": ("выкл.", "#9e9e9e"),
}


def parse_time_input(s: str) -> float:
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        pass
    if s.count(":") == 1 and "." not in s:
        s += ".0"
    if "." not in s:
        s += ".0"
    return parse_ts(s)


class LinesModel(QAbstractTableModel):
    edited = Signal(str)   # id реплики

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None

    def set_project(self, p: Optional[Project]) -> None:
        self.beginResetModel()
        self.project = p
        self.endResetModel()

    def reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def line(self, row: int) -> Line:
        assert self.project is not None
        return self.project.lines[row]

    def row_of(self, line_id: str) -> int:
        if not self.project:
            return -1
        for i, ln in enumerate(self.project.lines):
            if ln.id == line_id:
                return i
        return -1

    def refresh_line(self, line_id: str) -> None:
        r = self.row_of(line_id)
        if r >= 0:
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLS) - 1))

    def refresh_all(self) -> None:
        if self.project and self.project.lines:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.project.lines) - 1, len(COLS) - 1))

    # ---------------------------------------------------------- Qt API
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or not self.project else len(self.project.lines)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() in (C_START, C_END, C_CHAR, C_TEXT):
            f |= Qt.ItemIsEditable
        if index.column() == C_NUM:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not self.project:
            return None
        ln = self.project.lines[index.row()]
        c = index.column()
        ch = self.project.character(ln.character_id)
        if role == Qt.DisplayRole:
            if c == C_NUM:
                return index.row() + 1
            if c == C_START:
                return fmt_clock(ln.start)
            if c == C_END:
                return fmt_clock(ln.end)
            if c == C_CHAR:
                return ch.name if ch else ""
            if c == C_TEXT:
                return ln.text
            if c == C_STATUS:
                return STATUS_TEXT[line_status(self.project, ln)][0]
        elif role == Qt.EditRole:
            if c == C_START:
                return fmt_clock(ln.start)
            if c == C_END:
                return fmt_clock(ln.end)
            if c == C_CHAR:
                return ln.character_id or ""
            if c == C_TEXT:
                return ln.text
        elif role == Qt.CheckStateRole and c == C_NUM:
            return Qt.Checked if ln.enabled else Qt.Unchecked
        elif role == Qt.ForegroundRole:
            if c == C_STATUS:
                return QBrush(QColor(STATUS_TEXT[line_status(self.project, ln)][1]))
            if not ln.enabled:
                return QBrush(QColor("#9e9e9e"))
        elif role == Qt.DecorationRole and c == C_CHAR and ch:
            return QColor(ch.color)
        elif role == Qt.ToolTipRole:
            if c == C_TEXT:
                return ln.text + (f"\n\nЗаметка: {ln.note}" if ln.note else "")
            if c == C_NUM:
                return "Снимите галочку, чтобы исключить реплику из озвучки"
        elif role == Qt.UserRole:
            return ln.id
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or not self.project:
            return False
        ln = self.project.lines[index.row()]
        c = index.column()
        if role == Qt.CheckStateRole and c == C_NUM:
            ln.enabled = (Qt.CheckState(value) == Qt.Checked)
        elif role == Qt.EditRole:
            try:
                if c == C_START:
                    ln.start = max(0.0, parse_time_input(str(value)))
                    if ln.end <= ln.start:
                        ln.end = ln.start + 1.0
                elif c == C_END:
                    ln.end = max(ln.start + 0.05, parse_time_input(str(value)))
                elif c == C_CHAR:
                    ln.character_id = value or None
                elif c == C_TEXT:
                    ln.text = str(value).strip()
                else:
                    return False
            except ValueError:
                return False
        else:
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLS) - 1))
        self.edited.emit(ln.id)
        return True


class LinesFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.character_id: Optional[str] = "*"   # "*" — все
        self.status = "*"

    def set_filter(self, text: str, character_id: Optional[str], status: str) -> None:
        self.text, self.character_id, self.status = text.lower(), character_id, status
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        m: LinesModel = self.sourceModel()
        if not m.project:
            return True
        ln = m.project.lines[row]
        if self.text and self.text not in ln.text.lower():
            return False
        if self.character_id != "*" and ln.character_id != self.character_id:
            return False
        if self.status != "*" and line_status(m.project, ln) != self.status:
            return False
        return True


class CharacterDelegate(QStyledItemDelegate):
    def __init__(self, model: LinesModel, parent=None):
        super().__init__(parent)
        self.model = model

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItem("— без роли —", "")
        if self.model.project:
            for c in self.model.project.characters:
                cb.addItem(c.name, c.id)
                cb.setItemData(cb.count() - 1, QColor(c.color), Qt.DecorationRole)
        return cb

    def setEditorData(self, editor, index):
        cid = index.data(Qt.EditRole)
        i = editor.findData(cid)
        editor.setCurrentIndex(max(0, i))
        editor.showPopup()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData(), Qt.EditRole)
