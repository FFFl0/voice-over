"""Окно настройки горячих клавиш."""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QKeySequenceEdit, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..core.hotkeys import SINGLE, action_key, conflicts


class HotkeysDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Горячие клавиши")
        self.resize(640, 640)
        info = QLabel("Щёлкните по сочетанию и нажмите новые клавиши. Педаль или кнопка на "
                      "микрофоне обычно работают как клавиша — нажмите её, чтобы назначить. "
                      "Клавиши без Ctrl/Alt не срабатывают, пока вы печатаете текст.")
        info.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Действие", "Клавиши"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 200)
        self.table.verticalHeader().setVisible(False)
        self.rows: list[tuple[str, str, QKeySequenceEdit]] = []   # ключ, по умолчанию, редактор
        hk = win.settings.hotkeys
        defaults = getattr(win, "_default_shortcuts", {})
        for k, (title, default) in SINGLE.items():
            self._add(k, title, hk.get(k, default), default)
        seen = set()
        for a in win.findChildren(QAction):
            t = a.text().replace("&", "")
            k = action_key(a.text())
            if not t or k in seen or a.isSeparator() or a.menu() is not None:
                continue
            seen.add(k)
            self._add(k, t.replace("…", ""), a.shortcut().toString(), defaults.get(k, ""))
        reset = QPushButton("Вернуть по умолчанию")
        reset.clicked.connect(self._reset)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addWidget(reset)
        lay.addWidget(bb)

    def _add(self, key: str, title: str, seq: str, default: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(title)
        it.setFlags(it.flags() & ~it.flags().ItemIsEditable)
        self.table.setItem(r, 0, it)
        ed = QKeySequenceEdit(QKeySequence(seq))
        ed.setMaximumSequenceLength(1)
        ed.setClearButtonEnabled(True)
        self.table.setCellWidget(r, 1, ed)
        self.rows.append((key, default, ed))

    def _reset(self):
        for _k, default, ed in self.rows:
            ed.setKeySequence(QKeySequence(default))

    def values(self) -> dict[str, str]:
        return {k: ed.keySequence().toString() for k, _d, ed in self.rows}

    def _ok(self):
        vals = self.values()
        bad = conflicts(vals)
        if bad:
            names = {k: self.table.item(i, 0).text() for i, (k, _d, _e) in enumerate(self.rows)}
            QMessageBox.warning(self, "Горячие клавиши", "Одинаковые сочетания:\n" + "\n".join(
                f"{seq}: «{names[a]}» и «{names[b]}»" for seq, a, b in bad[:8]))
            return
        defaults = {k: d for k, d, _e in self.rows}
        # храним только отличия от значений по умолчанию
        self.win.settings.hotkeys = {k: v for k, v in vals.items() if v != defaults.get(k, "")}
        self.accept()
