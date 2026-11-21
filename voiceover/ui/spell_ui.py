"""Проверка орфографии и ударения в поле текста реплики."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import QMenu, QPlainTextEdit

from ..core.spell import WORD_RE, SpellChecker, toggle_accent
from . import theme
from .workers import Task, pool

LANG_TITLES = {"ru": "русский", "en": "английский", "uk": "украинский", "": "выключена"}


class SpellHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, checker: SpellChecker):
        super().__init__(doc)
        self.checker = checker

    def highlightBlock(self, text: str) -> None:
        if not self.checker.ready:
            return
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        fmt.setUnderlineColor(QColor(theme.current().error))
        for start, length, _w in self.checker.errors(text):
            self.setFormat(start, length, fmt)


class SpellSupport:
    """Подключает к QPlainTextEdit подсветку ошибок, меню исправлений и ударение (Ctrl+')."""

    def __init__(self, edit: QPlainTextEdit, lang: str, on_change=None):
        self.edit = edit
        self.on_change = on_change
        self.checker: Optional[SpellChecker] = None
        self.hl: Optional[SpellHighlighter] = None
        self._task = None
        edit.setContextMenuPolicy(Qt.CustomContextMenu)
        edit.customContextMenuRequested.connect(self._menu)
        for seq in ("Ctrl+'", "Alt+'"):
            sc = QShortcut(QKeySequence(seq), edit)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(self.toggle_accent)
        self.set_language(lang)

    def set_language(self, lang: str) -> None:
        if self.hl is not None:
            self.hl.setDocument(None)
            self.hl = None
        self.checker = None
        if not lang:
            return
        checker = SpellChecker(lang)
        self.checker = checker

        def work(t: Task):
            return checker.load()

        def done(ok):
            if self.checker is checker and ok:
                self.hl = SpellHighlighter(self.edit.document(), checker)
                self.hl.rehighlight()

        task = Task("Словарь", work)
        task.signals.result.connect(done)
        self._task = task
        pool().start(task)

    def set_known_words(self, words) -> None:
        if self.checker:
            self.checker.set_extra(words)
            if self.hl:
                self.hl.rehighlight()

    # ------------------------------------------------------------ ударение
    def toggle_accent(self) -> None:
        c = self.edit.textCursor()
        text = self.edit.toPlainText()
        new, pos = toggle_accent(text, c.position())
        if new == text:
            return
        c.beginEditBlock()
        c.select(QTextCursor.Document)
        c.insertText(new)
        c.endEditBlock()
        c.setPosition(min(pos, len(new)))
        self.edit.setTextCursor(c)

    # ------------------------------------------------------------ меню
    def _word_at(self, pos):
        c = self.edit.cursorForPosition(pos)
        text = c.block().text()
        i = c.positionInBlock()
        for m in WORD_RE.finditer(text):
            if m.start() <= i <= m.end():
                return c.block().position() + m.start(), m.group()
        return None, None

    def _menu(self, pos):
        menu: QMenu = self.edit.createStandardContextMenu()
        first = menu.actions()[0] if menu.actions() else None
        start, word = self._word_at(pos)
        extra = []
        if word and self.checker and self.checker.ready and not self.checker.check(word):
            sugg = self.checker.suggest(word)
            for s in sugg:
                a = menu.addAction(s)
                a.triggered.connect(lambda _=False, s=s, st=start, w=word: self._replace(st, w, s))
                extra.append(a)
            if not sugg:
                a = menu.addAction("Нет вариантов")
                a.setEnabled(False)
                extra.append(a)
            a = menu.addAction(f"Добавить «{word}» в словарь")
            a.triggered.connect(lambda _=False, w=word: self._add(w))
            extra.append(a)
        acc = menu.addAction("Поставить / снять ударение\tCtrl+'")
        acc.triggered.connect(self.toggle_accent)
        extra.append(acc)
        sep = menu.addSeparator()
        if first is not None:
            for a in extra + [sep]:
                menu.removeAction(a)
            menu.insertActions(first, extra)
            menu.insertSeparator(first)
        menu.exec(self.edit.mapToGlobal(pos))

    def _replace(self, start: int, word: str, new: str) -> None:
        c = self.edit.textCursor()
        c.setPosition(start)
        c.setPosition(start + len(word), QTextCursor.KeepAnchor)
        c.insertText(new)

    def _add(self, word: str) -> None:
        if self.checker:
            self.checker.add_word(word)
            if self.hl:
                self.hl.rehighlight()
