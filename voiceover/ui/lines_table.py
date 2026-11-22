"""Таблица реплик: модель, фильтр и делегат выбора персонажа."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
                            Signal)
from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QComboBox, QStyle, QStyledItemDelegate, QTableView

from ..core.models import Line, Project, line_status
from ..core.rythmo import rate_level, speech_rate
from ..core.subtitles import fmt_clock, parse_ts
from . import theme

COLS = ["№", "Начало", "Конец", "Персонаж", "Текст", "Статус", "Темп", "Оригинал"]
C_NUM, C_START, C_END, C_CHAR, C_TEXT, C_STATUS, C_RATE, C_ORIG = range(8)
RATE_TIP = ("Темп: слогов в секунду, которые нужно произнести в отведённое время.\n"
            "До 6.5 — спокойно, до 8 — быстро, больше — актёр не успеет:\n"
            "сократите текст или растяните реплику.")

STATUS_TEXT = {
    "rec": "записано",
    "ok": "одобрено ✓",
    "redo": "переписать",
    "none": "не записано",
    "nochar": "нет роли",
    "off": "выкл.",
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
        if index.column() in (C_START, C_END, C_CHAR, C_TEXT, C_ORIG):
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
                return STATUS_TEXT[line_status(self.project, ln)]
            if c == C_RATE:
                r = speech_rate(ln)
                return f"{r:.1f}" if r else ""
            if c == C_ORIG:
                return ln.orig_text
        elif role == Qt.EditRole:
            if c == C_START:
                return fmt_clock(ln.start)
            if c == C_END:
                return fmt_clock(ln.end)
            if c == C_CHAR:
                return ln.character_id or ""
            if c == C_TEXT:
                return ln.text
            if c == C_ORIG:
                return ln.orig_text
        elif role == Qt.CheckStateRole and c == C_NUM:
            return Qt.Checked if ln.enabled else Qt.Unchecked
        elif role == Qt.ForegroundRole and c == C_RATE:
            lvl = rate_level(speech_rate(ln))
            th = theme.current()
            return QBrush(QColor({"ok": th.muted, "fast": th.warn, "too_fast": th.error}[lvl]))
        elif role == Qt.ForegroundRole:
            if not ln.enabled or c in (C_NUM, C_START, C_END, C_ORIG):
                return QBrush(QColor(theme.current().muted))
        elif role == Qt.FontRole and c in (C_NUM, C_START, C_END, C_RATE):
            return theme.body_font(theme.TEXT_SM, tabular=True)
        elif role == Qt.UserRole + 1:   # ключ статуса для делегата
            return line_status(self.project, ln)
        elif role == Qt.UserRole + 2 and ch:   # цвет роли
            return ch.color
        elif role == Qt.ToolTipRole:
            if c in (C_TEXT, C_ORIG):
                tip = ln.text
                if ln.orig_text:
                    tip += f"\n\nОригинал: {ln.orig_text}"
                tip += f"\n\nЗаметка: {ln.note}" if ln.note else ""
                if ln.review == "redo":
                    tip += "\n\nПереписать" + (f": {ln.review_note}" if ln.review_note else "")
                from html import escape   # подсказка Qt распознаёт HTML — показываем как текст
                return "<qt>" + escape(tip).replace("\n", "<br>") + "</qt>"
            if c == C_RATE:
                return RATE_TIP
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
                elif c == C_ORIG:
                    ln.orig_text = str(value).strip()
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
        if self.status == "fast":
            return rate_level(speech_rate(ln)) != "ok"
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


class StatusDelegate(QStyledItemDelegate):
    """Статус реплики — слово цвета состояния, без плашек и точек."""

    def paint(self, p: QPainter, option, index):
        self.initStyleOption(option, index)
        option.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, option, p, option.widget)
        key = index.data(Qt.UserRole + 1)
        if not key:
            return
        p.save()
        p.setFont(theme.body_font(theme.TEXT_SM, QFont.Medium))
        p.setPen(QColor(theme.status_colors().get(key, theme.current().muted)))
        p.drawText(option.rect.adjusted(8, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   STATUS_TEXT.get(key, ""))
        p.restore()


class RoleDelegate(QStyledItemDelegate):
    """Колонка персонажа: цветная точка + имя; редактор — выпадающий список."""

    def __init__(self, model: "LinesModel", parent=None):
        super().__init__(parent)
        self.model = model
        self._combo = CharacterDelegate(model, parent)

    def paint(self, p: QPainter, option, index):
        self.initStyleOption(option, index)
        name = option.text
        option.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, option, p, option.widget)
        color = index.data(Qt.UserRole + 2)
        p.save()
        x = option.rect.left() + 8
        if color:   # квадратный ключ цвета роли, как на таймлайне
            p.fillRect(QRectF(x, option.rect.center().y() - 4, 8, 8), QColor(color))
        selected = bool(option.state & QStyle.State_Selected)
        p.setPen(option.palette.color(QPalette.HighlightedText if selected else QPalette.Text))
        p.drawText(option.rect.adjusted(22, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   option.fontMetrics.elidedText(name, Qt.ElideRight, option.rect.width() - 26))
        p.restore()

    def createEditor(self, parent, option, index):
        return self._combo.createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        self._combo.setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        self._combo.setModelData(editor, model, index)


class LinesView(QTableView):
    EMPTY_TEXT = ("Реплик пока нет.\n\nИмпортируйте субтитры (кнопка «Субтитры» вверху)\n"
                  "или нажмите «Распознать», чтобы получить текст из звука.\n"
                  "Добавить вручную — «+ Реплика» внизу или клавиша N.")
    FILTERED_TEXT = "Нет реплик, подходящих под фильтр.\nИзмените поиск, персонажа или статус."

    """Таблица реплик с заметной рамкой вокруг выделенных строк.

    Фон выделения бывает едва отличим от строки, поэтому поверх рисуется рамка
    цвета акцента: у текущей реплики толще, у остальных выделенных — тоньше."""

    def paintEvent(self, e):
        super().paintEvent(e)
        model = self.model()
        if model is not None and model.rowCount() == 0:   # подсказка вместо пустой таблицы
            src = model.sourceModel() if hasattr(model, "sourceModel") else model
            empty = src is None or src.rowCount() == 0
            if src is not None and getattr(src, "project", None) is None:
                return
            p = QPainter(self.viewport())
            p.setPen(QColor(theme.current().muted))
            p.setFont(theme.body_font(theme.TEXT_MD))
            p.drawText(self.viewport().rect().adjusted(20, 20, -20, -20), Qt.AlignCenter,
                       self.EMPTY_TEXT if empty else self.FILTERED_TEXT)
            p.end()
            return
        sm = self.selectionModel()
        if sm is None or model is None or model.columnCount() == 0:
            return
        rows = sorted({i.row() for i in sm.selectedRows()})
        if not rows:
            return
        cur = self.currentIndex().row()
        last_col = model.columnCount() - 1
        while last_col > 0 and self.isColumnHidden(last_col):
            last_col -= 1
        accent = QColor(theme.current().accent)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing, True)
        fill = QColor(accent)
        fill.setAlpha(28)
        for r in rows:
            rect = self.visualRect(model.index(r, 0)).united(self.visualRect(model.index(r, last_col)))
            if not rect.isValid() or not rect.intersects(e.rect()):
                continue
            rect.setLeft(0)
            rect.setRight(self.viewport().width() - 1)
            p.fillRect(rect, fill)
            width = 2.5 if r == cur else 1.5
            p.setPen(QPen(accent, width))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5), 3, 3)
            if r == cur:   # полоса слева у текущей реплики
                p.fillRect(QRectF(rect.left() + 1, rect.top() + 2, 4, rect.height() - 4), accent)
        p.end()
