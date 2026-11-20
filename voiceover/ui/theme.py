"""Оформление: тёмная/светлая тема, стили виджетов и векторные иконки."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    window: str      # фон окна
    panel: str       # фон панелей и тулбара
    base: str        # фон полей ввода и таблиц
    alt: str         # чередующиеся строки
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    danger: str
    ok: str
    warn: str
    info: str
    lane: str        # фон дорожек таймлайна
    lane_alt: str
    wave: str        # волна звука на таймлайне
    video_bg: str

    @property
    def dark(self) -> bool:
        return QColor(self.window).lightness() < 128


DARK = Theme(
    name="dark", window="#17181c", panel="#1f2026", base="#25262d", alt="#2a2b33",
    border="#34353f", text="#e8e8ee", muted="#8d8f9c", accent="#8b6cff", accent_text="#ffffff",
    danger="#ff5c69", ok="#3ecf8e", warn="#ffb547", info="#5aa9ff",
    lane="#1c1d22", lane_alt="#212228", wave="#3a3d4a", video_bg="#0c0c0f",
)
LIGHT = Theme(
    name="light", window="#f3f3f6", panel="#ffffff", base="#ffffff", alt="#f7f7fa",
    border="#dcdce3", text="#1d1d24", muted="#6e7080", accent="#6c4cf5", accent_text="#ffffff",
    danger="#e5383b", ok="#1f9d63", warn="#d98a00", info="#2d7ff0",
    lane="#fafafc", lane_alt="#f2f2f6", wave="#cfd1db", video_bg="#111114",
)
THEMES = {"dark": DARK, "light": LIGHT}

_current: Theme = DARK


def current() -> Theme:
    return _current


def status_colors() -> dict[str, str]:
    t = _current
    return {"ok": t.ok, "rec": t.info, "none": t.danger, "stale": t.warn,
            "nochar": t.muted, "off": t.muted}


def _check_svg(t: Theme) -> str:
    """Галочка для чекбоксов: QSS принимает картинку только файлом."""
    from ..core.settings import data_dir
    path = data_dir() / "theme" / f"check_{t.name}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{t.accent_text}" stroke-width="3" stroke-linecap="round" '
                        f'stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
                        encoding="utf-8")
    return path.as_posix()


def _arrow_svg(t, direction: str, disabled: bool = False) -> str:
    """Стрелка для списков и счётчиков: QSS принимает картинку только файлом."""
    from ..core.settings import data_dir
    col = t.muted if disabled else t.text
    path = data_dir() / "theme" / f"arrow_{direction}_{col.strip('#')}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        d = "M6 9l6 6 6-6" if direction == "down" else "M6 15l6-6 6 6"
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{col}" stroke-width="2.4" stroke-linecap="round" '
                        f'stroke-linejoin="round"><path d="{d}"/></svg>', encoding="utf-8")
    return path.as_posix()


def _qss(t: Theme) -> str:
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QWidget {{ color: {t.text}; font-size: 10pt; }}
QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border};
           padding: 5px 8px; border-radius: 6px; }}

QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.alt}; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolBar {{ background: {t.panel}; border: none; border-bottom: 1px solid {t.border};
           padding: 6px 8px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {t.border}; margin: 4px 8px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
              padding: 6px 10px; }}
QToolButton:hover {{ background: {t.alt}; border-color: {t.border}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.border}; }}
QToolButton:disabled {{ color: {t.muted}; }}
QToolButton#primaryAction {{ background: {t.accent}; color: {t.accent_text}; font-weight: 600; }}
QToolButton#primaryAction:hover {{ background: {QColor(t.accent).lighter(112).name()}; }}
QToolButton#primaryAction:disabled {{ background: {t.border}; color: {t.muted}; }}

QPushButton {{ background: {t.base}; border: 1px solid {t.border}; border-radius: 7px;
              padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.alt}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.alt}; border-color: {t.border}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent};
                              font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {QColor(t.accent).lighter(112).name()}; }}
QPushButton[primary="true"]:disabled {{ background: {t.border}; color: {t.muted}; border-color: {t.border}; }}
QPushButton[danger="true"] {{ color: {t.danger}; font-weight: 600; }}
QPushButton[danger="true"]:hover {{ border-color: {t.danger}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[flat="true"]:hover {{ background: {t.alt}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.base}; border: 1px solid {t.border}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled {{ color: {t.muted}; background: {t.alt}; }}
QComboBox::drop-down {{ border: 0; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({_arrow_svg(t, "down")}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({_arrow_svg(t, "down", True)}); }}
QComboBox::down-arrow:on {{ image: url({_arrow_svg(t, "up")}); }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_arrow_svg(t, "up")}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_arrow_svg(t, "down")}); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url({_arrow_svg(t, "up", True)}); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: url({_arrow_svg(t, "down", True)}); }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 20px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t.alt}; }}
QComboBox QAbstractItemView {{ background: {t.panel}; border: 1px solid {t.border};
    border-radius: 6px; selection-background-color: {t.accent}; selection-color: {t.accent_text};
    padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 10px; background: {t.panel};
                    top: -1px; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 8px 16px; margin-right: 2px;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}

QTableView, QListWidget, QTreeView {{ background: {t.base}; alternate-background-color: {t.alt};
    border: 1px solid {t.border}; border-radius: 8px; gridline-color: transparent;
    selection-background-color: {QColor(t.accent).darker(105).name() if t.dark else QColor(t.accent).lighter(170).name()};
    selection-color: {t.text if not t.dark else '#ffffff'}; }}
QTableView::item {{ padding: 0 6px; border: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; margin: 1px 3px; }}
QListWidget::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QListWidget::item:hover:!selected {{ background: {t.alt}; }}
QHeaderView::section {{ background: {t.panel}; color: {t.muted}; border: none;
    border-bottom: 1px solid {t.border}; padding: 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {t.panel}; border: none; }}

QGroupBox {{ border: 1px solid {t.border}; border-radius: 10px; margin-top: 14px; padding: 12px 8px 8px 8px;
            font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {t.muted}; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {t.border};
    background: {t.base}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent};
    image: url({_check_svg(t)}); }}
QRadioButton::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}

QProgressBar {{ background: {t.alt}; border: none; border-radius: 4px; height: 8px; text-align: center;
               color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
QProgressBar#level::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {t.ok},
    stop:0.7 {t.warn}, stop:1 {t.danger}); }}

QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.text}; width: 12px; height: 12px; margin: -5px 0;
    border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {t.accent}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {t.border}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {t.window}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {t.accent}; }}

QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.border}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}

QLabel#muted {{ color: {t.muted}; }}
QLabel#h1 {{ font-size: 22pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QFrame#card {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#prompt {{ background: {t.base}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#nowLine {{ background: {t.panel}; border-top: 1px solid {t.border}; }}
QWidget#videoArea {{ background: {t.video_bg}; }}
"""


def apply_theme(app: QApplication, name: str) -> Theme:
    global _current
    t = THEMES.get(name, DARK)
    _current = t
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(t.window))
    pal.setColor(QPalette.WindowText, c(t.text))
    pal.setColor(QPalette.Base, c(t.base))
    pal.setColor(QPalette.AlternateBase, c(t.alt))
    pal.setColor(QPalette.Text, c(t.text))
    pal.setColor(QPalette.Button, c(t.base))
    pal.setColor(QPalette.ButtonText, c(t.text))
    pal.setColor(QPalette.ToolTipBase, c(t.panel))
    pal.setColor(QPalette.ToolTipText, c(t.text))
    pal.setColor(QPalette.Highlight, c(t.accent))
    pal.setColor(QPalette.HighlightedText, c(t.accent_text))
    pal.setColor(QPalette.PlaceholderText, c(t.muted))
    pal.setColor(QPalette.Mid, c(t.border))
    pal.setColor(QPalette.Midlight, c(t.alt))
    pal.setColor(QPalette.Link, c(t.accent))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(t.muted))
    app.setPalette(pal)
    app.setStyleSheet(_qss(t))
    _icon_cache.clear()
    return t


# ============================================================================ иконки
# Собственный набор контурных иконок 24×24 (stroke = currentColor).

_ICONS: dict[str, str] = {
    "new": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
           '<path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
            '<path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    "subs": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M14 15h3M7 11h2M12 11h5"/>',
    "whisper": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'
               '<path d="M2 9v4M22 9v4"/>',
    "separate": '<path d="M12 3 3 8l9 5 9-5z"/><path d="m3 13 9 5 9-5"/><path d="M3 17.5 12 22l9-4.5" opacity=".5"/>',
    "synth": '<path d="M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/>'
             '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8zM5 16l.6 1.4L7 18l-1.4.6L5 20l-.6-1.4L3 18l1.4-.6z"/>',
    "preview": '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l5.5-3.5z"/>',
    "export": '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "play": '<path d="M7 4.5v15L19 12z" fill="currentColor"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/>'
             '<rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/>',
    "back": '<path d="M11 7v10l-7-5zM20 7v10l-7-5z" fill="currentColor"/>',
    "fwd": '<path d="M13 7v10l7-5zM4 7v10l7-5z" fill="currentColor"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "record": '<circle cx="12" cy="12" r="7" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 13.5a7 7 0 0 1 4 6.5"/>',
    "settings": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/>'
                '<circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "volume": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M3 12h18"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "translate": '<path d="M3 5h9M7.5 3v2M5 5c.5 3.5 3 6.5 6.5 8M10 5c-.5 3.5-3 6.5-6.5 8"/>'
                 '<path d="M13 21l4.5-10 4.5 10M14.6 17.5h5.8"/>',
    "ocr": '<path d="M3 8V4h4M21 8V4h-4M3 16v4h4M21 16v4h-4"/><path d="M7 10h10M7 14h7"/>',
    "headphones": '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><rect x="3" y="14" width="4" height="7" rx="1.5"/>'
                  '<rect x="17" y="14" width="4" height="7" rx="1.5"/>',
}

_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """Иконка из набора, окрашенная в цвет текста темы (или заданный)."""
    color = color or _current.text
    key = (name, color, size)
    if key in _icon_cache:
        return _icon_cache[key]
    body = _ICONS.get(name, "")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
           f'color="{color}">{body.replace("currentColor", color)}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm, QIcon.Normal)
    # приглушённый вариант для неактивного состояния
    dis = QColor(_current.muted).name()
    if dis != color:
        svg_d = svg.replace(color, dis)
        r2 = QSvgRenderer(QByteArray(svg_d.encode()))
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        r2.render(p, QRectF(0, 0, size * 2, size * 2))
        p.end()
        pm.setDevicePixelRatio(2)
        ic.addPixmap(pm, QIcon.Disabled)
    _icon_cache[key] = ic
    return ic


def app_icon() -> QIcon:
    """Значок приложения: фиолетовый скруглённый квадрат с микрофоном и волной."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           '<stop offset="0" stop-color="#9b7bff"/><stop offset="1" stop-color="#5a3df0"/></linearGradient></defs>'
           '<rect x="2" y="2" width="60" height="60" rx="14" fill="url(#g)"/>'
           '<g fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round">'
           '<rect x="25" y="12" width="14" height="26" rx="7" fill="#fff"/>'
           '<path d="M17 31a15 15 0 0 0 30 0M32 46v7"/>'
           '<path d="M8 28v8M54 28v8" opacity=".7"/></g></svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        r.render(p, QRectF(0, 0, s, s))
        p.end()
        ic.addPixmap(pm)
    return ic
