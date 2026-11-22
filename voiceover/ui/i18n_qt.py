"""Подключение языков интерфейса к Qt.

Методы, через которые текст попадает на экран (setText, setToolTip, addItem, showMessage,
QMessageBox, QPainter.drawText…), подменяются обёртками: обёртка запоминает исходный
русский текст в свойстве виджета и показывает перевод. Текст из конструкторов
(QLabel("…"), QPushButton(icon, "…")) переводится при первой отрисовке виджета (Polish).
Смена языка проходит по всем виджетам и действиям и переводит их заново из исходников."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QPainter
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication, QComboBox,
                               QDoubleSpinBox, QFileDialog, QGroupBox, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QMenu, QMenuBar, QMessageBox,
                               QPlainTextEdit, QSpinBox, QStatusBar, QTableWidget, QTabWidget,
                               QTextEdit, QToolTip, QWidget)

from .. import i18n
from ..i18n import tr

ORIG = Qt.UserRole + 3840          # исходный текст пункта списка
_installed = False
_RAW: dict = {}


def _keep(obj, name: str, value):
    """Запомнить исходный текст в свойстве объекта (для смены языка)."""
    if isinstance(value, str):
        try:
            obj.setProperty("_i18n_" + name, value)
        except RuntimeError:
            pass
    return tr(value)


def _orig(obj, name: str, current: str) -> str:
    v = obj.property("_i18n_" + name)
    if isinstance(v, str):
        return v
    obj.setProperty("_i18n_" + name, current)
    return current


def _wrap_setter(cls, method: str, prop: str):
    raw = getattr(cls, method)

    def setter(self, value, *a):
        return raw(self, _keep(self, prop, value), *a)
    setter._i18n_raw = raw
    setattr(cls, method, setter)


def _wrap_args(owner, method: str, static: bool = False, only=None):
    """Перевести строковые аргументы (все или только с номерами из only):
    пути к файлам и введённые значения не переводятся."""
    raw = getattr(owner, method)

    def call(*args, **kw):
        args = tuple(tr(a) if isinstance(a, str) and (only is None or i in only) else a
                     for i, a in enumerate(args))
        kw = {k: tr(v) if isinstance(v, str) and only is None else v for k, v in kw.items()}
        return raw(*args, **kw)
    call._i18n_raw = raw
    setattr(owner, method, staticmethod(call) if static else call)


def _wrap_combo():
    raw_add, raw_insert, raw_set = QComboBox.addItem, QComboBox.insertItem, QComboBox.setItemText

    def add_item(self, *a):
        a = list(a)
        for i, v in enumerate(a):
            if isinstance(v, str):
                raw_add(self, *a[:i], tr(v), *a[i + 1:])
                self.setItemData(self.count() - 1, v, ORIG)
                return
        raw_add(self, *a)

    def insert_item(self, index, *a):
        a = list(a)
        for i, v in enumerate(a):
            if isinstance(v, str):
                raw_insert(self, index, *a[:i], tr(v), *a[i + 1:])
                self.setItemData(index, v, ORIG)
                return
        raw_insert(self, index, *a)

    def set_item_text(self, index, text):
        self.setItemData(index, text, ORIG)
        raw_set(self, index, tr(text))
    _RAW["combo_set"] = raw_set

    def add_items(self, texts):
        for t in texts:
            self.addItem(t)

    QComboBox.addItem, QComboBox.insertItem = add_item, insert_item
    QComboBox.setItemText, QComboBox.addItems = set_item_text, add_items


def _wrap_tabs():
    raw_add, raw_insert, raw_set = QTabWidget.addTab, QTabWidget.insertTab, QTabWidget.setTabText

    def fix(self, index, text):
        if isinstance(text, str):
            self.tabBar().setTabData(index, text)
            raw_set(self, index, tr(text))

    def add_tab(self, w, *a):
        i = raw_add(self, w, *a)
        fix(self, i, a[-1])
        return i

    def insert_tab(self, index, w, *a):
        i = raw_insert(self, index, w, *a)
        fix(self, i, a[-1])
        return i

    QTabWidget.addTab, QTabWidget.insertTab = add_tab, insert_tab
    QTabWidget.setTabText = lambda self, i, text: fix(self, i, text)


def _wrap_menus():
    for cls in (QMenu, QMenuBar):
        raw_add_menu = cls.addMenu

        def add_menu(self, *a, _raw=raw_add_menu):
            if a and isinstance(a[-1], str):
                m = _raw(self, *a[:-1], tr(a[-1]))
                m.menuAction().setProperty("_i18n_text", a[-1])
                return m
            return _raw(self, *a)
        cls.addMenu = add_menu
    raw_add_action = QMenu.addAction

    def add_action(self, *a):
        if a and isinstance(a[0], str):
            act = raw_add_action(self, tr(a[0]), *a[1:])
            act.setProperty("_i18n_text", a[0])
            return act
        if len(a) >= 2 and isinstance(a[1], str):
            act = raw_add_action(self, a[0], tr(a[1]), *a[2:])
            act.setProperty("_i18n_text", a[1])
            return act
        return raw_add_action(self, *a)
    QMenu.addAction = add_action
    _wrap_setter(QMenu, "setTitle", "title")


# ---------------------------------------------------------------- перевод объекта целиком
def translate_widget(w: QWidget) -> None:
    """Перевести собственные тексты виджета из сохранённых исходников."""
    try:
        if isinstance(w, QLabel):
            t = w.text()
            if w.textFormat() != Qt.RichText or "<" not in t:
                w.setText(_orig(w, "text", t))
        elif isinstance(w, QAbstractButton):
            w.setText(_orig(w, "text", w.text()))
        elif isinstance(w, QGroupBox):
            w.setTitle(_orig(w, "title", w.title()))
        elif isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            w.setPlaceholderText(_orig(w, "placeholderText", w.placeholderText()))
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                o = w.itemData(i, ORIG)
                if not isinstance(o, str):
                    o = w.itemText(i)
                    w.setItemData(i, o, ORIG)
                _RAW.get("combo_set", QComboBox.setItemText)(w, i, tr(o))
        elif isinstance(w, QTabWidget):
            bar = w.tabBar()
            for i in range(w.count()):
                o = bar.tabData(i)
                if not isinstance(o, str):
                    o = w.tabText(i)
                    bar.setTabData(i, o)
                bar.setTabText(i, tr(o))
        elif isinstance(w, (QDoubleSpinBox, QSpinBox)):
            w.setSuffix(_orig(w, "suffix", w.suffix()))
            w.setSpecialValueText(_orig(w, "specialValueText", w.specialValueText()))
        elif isinstance(w, QListWidget):
            for i in range(w.count()):
                it = w.item(i)
                o = it.data(ORIG)
                if not isinstance(o, str):
                    o = it.text()
                    it.setData(ORIG, o)
                if tr(o) != o or it.text() != o:
                    it.setText(tr(o))
        elif isinstance(w, QTableWidget):
            for r in range(w.rowCount()):
                for c in range(w.columnCount()):
                    it = w.item(r, c)
                    if it is None:
                        continue
                    o = it.data(ORIG)
                    if not isinstance(o, str):
                        o = it.text()
                        it.setData(ORIG, o)
                    it.setText(tr(o))
        if w.toolTip():
            w.setToolTip(_orig(w, "toolTip", w.toolTip()))
        if w.isWindow() and w.windowTitle():
            w.setWindowTitle(_orig(w, "windowTitle", w.windowTitle()))
        for a in w.actions():
            translate_action(a)
    except RuntimeError:
        pass


def translate_action(a: QAction) -> None:
    try:
        if a.isSeparator():
            return
        if a.text():
            a.setText(_orig(a, "text", a.text()))
        if a.toolTip() and a.toolTip() != a.text().replace("&", ""):
            a.setToolTip(_orig(a, "toolTip", a.toolTip()))
        if a.statusTip():
            a.setStatusTip(_orig(a, "statusTip", a.statusTip()))
        m = a.menu()
        if m is not None:
            for sub in m.actions():
                translate_action(sub)
    except RuntimeError:
        pass


def retranslate_all() -> None:
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        translate_widget(w)
        for a in w.findChildren(QAction):
            translate_action(a)
    for w in app.topLevelWidgets():
        w.update()
        for c in w.findChildren(QWidget):
            c.update()


class _Polisher(QObject):
    """Перевод текстов из конструкторов при первой отрисовке виджета."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish and isinstance(obj, QWidget) and i18n.current() != "ru":
            translate_widget(obj)
            if isinstance(obj, QMenu):
                for a in obj.actions():
                    translate_action(a)
        return False


def set_language(lang: str) -> None:
    """Сменить язык на лету: словарь, стандартные кнопки Qt, все открытые окна."""
    i18n.set_language(lang)
    app = QApplication.instance()
    if app is not None:
        install_qt_translator(app, i18n.current())
        retranslate_all()


def install_qt_translator(app, lang: str) -> None:
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    for t in getattr(app, "_translators", []):
        app.removeTranslator(t)
    app._translators = []
    QLocale.setDefault(QLocale(lang))
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    for name in (f"qtbase_{lang}", f"qt_{lang}"):
        tr_ = QTranslator(app)
        if tr_.load(name, path):
            app.installTranslator(tr_)
            app._translators.append(tr_)


def install(app, lang: str) -> None:
    """Подключить переводы (один раз за запуск) и выбрать язык."""
    global _installed
    if not _installed:
        _installed = True
        for cls, meth, prop in ((QLabel, "setText", "text"), (QAbstractButton, "setText", "text"),
                                (QAction, "setText", "text"), (QAction, "setToolTip", "toolTip"),
                                (QAction, "setStatusTip", "statusTip"),
                                (QWidget, "setToolTip", "toolTip"),
                                (QWidget, "setWindowTitle", "windowTitle"),
                                (QLineEdit, "setPlaceholderText", "placeholderText"),
                                (QPlainTextEdit, "setPlaceholderText", "placeholderText"),
                                (QTextEdit, "setPlaceholderText", "placeholderText"),
                                (QGroupBox, "setTitle", "title"),
                                (QDoubleSpinBox, "setSuffix", "suffix"),
                                (QSpinBox, "setSuffix", "suffix"),
                                (QAbstractSpinBox, "setSpecialValueText", "specialValueText")):
            _wrap_setter(cls, meth, prop)
        _wrap_combo()
        _wrap_tabs()
        _wrap_menus()
        _wrap_args(QStatusBar, "showMessage")
        _wrap_args(QMessageBox, "setText")
        _wrap_args(QMessageBox, "setInformativeText")
        for name in ("information", "warning", "critical", "question", "about"):
            _wrap_args(QMessageBox, name, static=True)
        for name in ("getText", "getItem", "getInt", "getDouble"):
            _wrap_args(QInputDialog, name, static=True, only={1, 2})     # заголовок, подпись
        for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName"):
            _wrap_args(QFileDialog, name, static=True, only={1, 3})      # заголовок, фильтр
        _wrap_args(QFileDialog, "getExistingDirectory", static=True, only={1})
        _wrap_args(QToolTip, "showText", static=True)
        _wrap_args(QPainter, "drawText")
        app._i18n_polisher = _Polisher(app)
        app.installEventFilter(app._i18n_polisher)
    i18n.set_language(lang)
    install_qt_translator(app, i18n.current())
