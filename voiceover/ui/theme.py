"""Оформление VoiceOver Studio: тема Midnight (Hallmark), стили виджетов, шрифты и иконки.

Это «таблица стилей» приложения: все цвета и шрифты интерфейса проходят через токены
класса Theme (аналог :root в CSS); в коде виджетов литеральных цветов нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

STAMP = """/* Hallmark · shape: app screen · genre: atmospheric · theme: Midnight (by request, deal 1/3, seed desktop-studio-multi-voice-dubbing-78f8·c508889d·2026-11-19·r0) · redesign: restyle
 * nav: kept · signature: kept, the cue under the picture with its lane lit on the timeline · states: empty (welcome, no lines, no take), busy (task bar), error (dialogs), full
 * axes: dark/grotesk-sans/cool-neutral · posture: committed · contrast: pass · v2.0.0 */
/* Hallmark · pre-emit critique: P2 H- E2 S2 R2 V2 · weakest: E, Qt has no outline or focus-visible, so focus is a 1px focus-colour edge on every control */
/* Hallmark · user edits over the theme: the ground lifted from 14% to 22% lightness and its chroma cut to a slate, the ink warmed to paper-white, radius 4px on controls and 6px on surfaces (the theme's do-nots say square and cold; the user's words win) */
/* Hallmark · faces: Manrope 300 for the timecode, figures and the cue, Manrope 400/500 for the interface (OFL, Cyrillic); the user turned down Unbounded and Golos Text, Mona Sans has no Cyrillic */
/* Base face is the application font, set in apply_theme; the timecode and figures get tabular figures in code, because a style-sheet font would drop the OpenType feature */
/* Hallmark · waive 26a · Qt style sheets have no outline property; focus shows as a border step in --focus, instantly */
/* Hallmark · waive theme-light · the user's light/dark switch is kept; the day mode is warm paper with the same slate ink and one azure */
"""


@dataclass(frozen=True)
class Theme:
    name: str
    paper: str       # грунт окна
    paper2: str      # панели, шаг светлее
    paper3: str      # поднятая поверхность, наведение, выделение
    ink: str
    ink2: str
    muted: str
    rule: str        # волосяная линия между строками
    rule2: str       # контур вторичных кнопок и полей, дорожка метра
    accent: str      # единственный сигнал: главное действие, состояние
    accent2: str
    accent_ink: str  # текст на акценте
    focus: str
    error: str
    success: str
    warn: str
    shadow: str
    on_role: str = "#0d1623"   # текст на блоках цвета роли (тональные цвета светлые в обеих темах)
    font_body: str = "Manrope"
    font_display: str = "Manrope"   # тот же шрифт в начертании Light 300

    @property
    def dark(self) -> bool:
        return QColor(self.paper).lightness() < 128

    # Совместимые имена, которыми пользуется остальной интерфейс
    @property
    def window(self) -> str: return self.paper
    @property
    def panel(self) -> str: return self.paper2
    @property
    def base(self) -> str: return self.paper
    @property
    def alt(self) -> str: return self.paper3
    @property
    def border(self) -> str: return self.rule
    @property
    def text(self) -> str: return self.ink
    @property
    def accent_text(self) -> str: return self.accent_ink
    @property
    def danger(self) -> str: return self.error
    @property
    def ok(self) -> str: return self.success
    @property
    def info(self) -> str: return self.ink2
    @property
    def lane(self) -> str: return self.paper
    @property
    def lane_alt(self) -> str: return self.paper2
    @property
    def wave(self) -> str: return self.rule2
    @property
    def video_bg(self) -> str: return self.paper


MIDNIGHT = Theme(
    name="dark",
    paper="#141b25", paper2="#1b232e", paper3="#252e3b",
    ink="#f2eee7", ink2="#dfd6cb", muted="#9fa9b3",
    rule="#303944", rule2="#4b5665",
    accent="#73cbf0", accent2="#4189ad", accent_ink="#0d1623", focus="#77ddff",
    error="#f38277", success="#79ca8b", warn="#e6b96b", shadow="#03060d", on_role="#0d1623",
)
MIDNIGHT_DAY = Theme(
    name="light",
    paper="#f8f6f3", paper2="#efece8", paper3="#e4e1db",
    ink="#1a222e", ink2="#303b4a", muted="#5a616b",
    rule="#dbd7d0", rule2="#b9b3aa",
    accent="#006c98", accent2="#3a83a6", accent_ink="#fcfaf6", focus="#007bb2",
    error="#b02b27", success="#0f6a31", warn="#845000", shadow="#b9b3aa", on_role="#0d1623",
)
THEMES = {"dark": MIDNIGHT, "light": MIDNIGHT_DAY}

# Цвета ролей: тональный ночной набор (одинаковая светлота и насыщенность, разные оттенки),
# чтобы дорожки различались, не споря с единственным азурным сигналом.
CHARACTER_PALETTE = [
    "#63bde7", "#bba2ea", "#ec9695", "#7fc48e", "#e2a16b", "#de97c6", "#52c6c0", "#c0b360",
    "#8ab2f4", "#ea9a7e", "#cf9bdb", "#a8bb6d", "#3a98c0", "#967dc2", "#c47171", "#5a9e6a",
    "#bb7c46", "#b772a0", "#1da09a", "#9b8d3a", "#668ccc", "#c2755a", "#a877b4", "#849548",
]

# Шкала типографики для экранов приложения: шаг 1.2 от 10.5 pt (≈14 px)
TEXT_SM = 9.0
TEXT_MD = 10.5
TEXT_LG = 12.6
TEXT_XL = 15.1
TEXT_2XL = 18.1
TEXT_3XL = 21.8

# Скругления: небольшие, одно на уровень вложенности
RADIUS_SM = 4   # кнопки, поля, блоки на таймлайне
RADIUS_MD = 6   # поверхности: колодцы, карточки, меню

_current: Theme = MIDNIGHT
_fonts_loaded = False


def current() -> Theme:
    return _current


def status_colors() -> dict[str, str]:
    t = _current
    return {"ok": t.success, "rec": t.ink2, "none": t.error, "stale": t.warn,
            "nochar": t.muted, "off": t.muted}


def contrast(a: str, b: str) -> float:
    """Контраст WCAG между двумя цветами."""
    def lum(h: str) -> float:
        c = QColor(h)
        out = []
        for x in (c.redF(), c.greenF(), c.blueF()):
            out.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def role_text(color: str, ground: str | None = None) -> str:
    """Цвет роли как цвет текста — только если он читается на фоне (≥ 4.5:1), иначе чернила."""
    ground = ground or _current.paper
    return color if contrast(color, ground) >= 4.5 else _current.ink


def load_fonts() -> None:
    """Подключить Manrope (SIL OFL) из assets/fonts."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    d = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    for f in sorted(d.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    _fonts_loaded = True


def display_font(size_pt: float, tabular: bool = False) -> QFont:
    """Manrope Light 300 — голос темы (заголовки, тайм-код, реплика)."""
    f = QFont(_current.font_display)
    f.setPointSizeF(size_pt)
    f.setWeight(QFont.Light)
    f.setLetterSpacing(QFont.PercentageSpacing, 98.5)
    if tabular:
        _tnum(f)
    return f


def body_font(size_pt: float = TEXT_MD, weight: QFont.Weight = QFont.Normal,
              tabular: bool = False) -> QFont:
    f = QFont(_current.font_body)
    f.setPointSizeF(size_pt)
    f.setWeight(weight)
    if tabular:
        _tnum(f)
    return f


def _tnum(f: QFont) -> None:
    try:
        from PySide6.QtGui import QFont as _F
        f.setFeature(_F.Tag("tnum"), 1)
    except Exception:  # noqa: BLE001  старый Qt без OpenType-фич
        pass


def _arrow_svg(t, direction: str, disabled: bool = False) -> str:
    """Стрелка для списков и счётчиков: QSS принимает картинку только файлом."""
    from ..core.settings import data_dir
    col = t.muted if disabled else t.ink
    path = data_dir() / "theme" / f"arrow_{direction}_{col.strip('#')}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        d = "M6 9l6 6 6-6" if direction == "down" else "M6 15l6-6 6 6"
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{col}" stroke-width="2.4" stroke-linecap="round" '
                        f'stroke-linejoin="round"><path d="{d}"/></svg>', encoding="utf-8")
    return path.as_posix()


def _qss(t: Theme) -> str:
    sel_text = t.ink
    return STAMP + f"""
* {{ outline: 0; }}
QWidget {{ color: {t.ink}; }}
QMainWindow, QDialog, QStackedWidget, QSplitter {{ background: {t.paper}; }}
QToolTip {{ background: {t.paper3}; color: {t.ink}; border: 0; padding: 6px 9px; border-radius: {RADIUS_SM}px; }}

QMenuBar {{ background: {t.paper}; border-bottom: 1px solid {t.rule}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; color: {t.muted}; border-radius: {RADIUS_SM}px; }}
QMenuBar::item:selected {{ background: {t.paper3}; color: {t.ink}; }}
QMenu {{ background: {t.paper2}; border: 1px solid {t.rule}; border-radius: {RADIUS_MD}px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: {RADIUS_SM}px; }}
QMenu::item:selected {{ background: {t.paper3}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.rule}; margin: 6px 0; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 8px; }}

QToolBar {{ background: {t.paper}; border: 0; border-bottom: 1px solid {t.rule}; padding: 6px 10px;
           spacing: 2px; }}
QToolBar::separator {{ width: 1px; background: {t.rule}; margin: 6px 10px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS_SM}px;
              padding: 7px 10px; color: {t.ink}; }}
QToolButton:hover {{ background: {t.paper3}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.paper2}; }}
QToolButton:focus {{ border-color: {t.focus}; }}
QToolButton:disabled {{ color: {t.muted}; }}
QToolButton#primaryAction {{ background: {t.accent}; color: {t.accent_ink}; font-weight: 500;
                            padding: 7px 16px; }}
QToolButton#primaryAction:hover {{ background: {t.focus}; }}
QToolButton#primaryAction:disabled {{ background: {t.paper3}; color: {t.muted}; }}
QLabel#groupLabel {{ color: {t.muted}; font-size: {TEXT_SM}pt; padding: 0 2px 0 8px; }}

QPushButton {{ background: transparent; border: 1px solid {t.rule2}; border-radius: {RADIUS_SM}px;
              padding: 7px 14px; min-height: 18px; font-weight: 500; }}
QPushButton:hover {{ background: {t.paper3}; }}
QPushButton:pressed {{ background: {t.paper2}; }}
QPushButton:focus {{ border-color: {t.focus}; }}
QPushButton:disabled {{ color: {t.muted}; border-color: {t.rule}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_ink}; border-color: {t.accent}; }}
QPushButton[primary="true"]:hover {{ background: {t.focus}; border-color: {t.focus}; }}
QPushButton[primary="true"]:focus {{ border-color: {t.ink}; }}
QPushButton[primary="true"]:disabled {{ background: {t.paper3}; color: {t.muted}; border-color: {t.paper3}; }}
QPushButton[danger="true"] {{ color: {t.error}; }}
QPushButton[quiet="true"] {{ border-color: transparent; }}
QPushButton[quiet="true"]:hover {{ background: {t.paper3}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.paper}; border: 1px solid {t.rule2}; border-radius: {RADIUS_SM}px; padding: 6px 8px;
    selection-background-color: {t.accent2}; selection-color: {t.ink}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {t.focus}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled {{ color: {t.muted}; border-color: {t.rule}; }}
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
QDoubleSpinBox::down-button:hover {{ background: {t.paper3}; }}
QComboBox QAbstractItemView {{ background: {t.paper2}; border: 1px solid {t.rule}; border-radius: {RADIUS_SM}px; padding: 4px;
    selection-background-color: {t.paper3}; selection-color: {t.ink}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{ width: 16px; border: 0; background: transparent; }}

QTabWidget::pane {{ border: 0; background: {t.paper2}; border-radius: {RADIUS_MD}px; border-top-left-radius: 0; }}
QTabBar {{ background: {t.paper}; }}
QTabBar::tab {{ background: {t.paper}; color: {t.muted}; padding: 10px 16px; margin: 0;
               border-top-left-radius: {RADIUS_MD}px; border-top-right-radius: {RADIUS_MD}px;
               border: 0; border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover {{ color: {t.ink}; }}
QTabBar::tab:selected {{ color: {t.ink}; background: {t.paper2}; border-bottom: 2px solid {t.accent}; }}
QTabBar::tab:focus {{ color: {t.ink}; }}

QTableView, QListWidget, QTreeView {{ background: {t.paper2}; border: 0; gridline-color: transparent;
    selection-background-color: {t.paper3}; selection-color: {sel_text}; }}
QTableView::item {{ padding: 0 8px; border: 0; border-bottom: 1px solid {t.rule}; }}
QTableView::item:selected {{ background: {t.paper3}; color: {sel_text}; }}
QTableView:focus, QListWidget:focus {{ border: 0; }}
QListWidget::item {{ padding: 9px 10px; border-bottom: 1px solid {t.rule}; }}
QListWidget#recent {{ border-radius: {RADIUS_MD}px; }}
QListWidget::item:selected {{ background: {t.paper3}; color: {sel_text}; }}
QListWidget::item:hover:!selected {{ background: {t.paper3}; }}
QHeaderView {{ background: {t.paper2}; }}
QHeaderView::section {{ background: {t.paper2}; color: {t.muted}; border: 0;
    border-bottom: 1px solid {t.rule2}; padding: 8px; font-weight: 500; font-size: {TEXT_SM}pt; }}
QTableCornerButton::section {{ background: {t.paper2}; border: 0; }}

QGroupBox {{ border: 0; border-top: 1px solid {t.rule}; margin-top: 22px; padding: 14px 0 0 0;
            font-weight: 500; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0; color: {t.muted}; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid {t.rule2};
    background: {t.paper}; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {t.ink}; border-color: {t.ink}; image: url({_check_svg(t)}); }}
QRadioButton::indicator:checked {{ background: {t.ink}; border: 4px solid {t.paper}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border-color: {t.focus}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.muted}; }}

QProgressBar {{ background: {t.rule2}; border: 0; border-radius: 1px; max-height: 2px; min-height: 2px;
               text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; }}
QProgressBar#level {{ max-height: 4px; min-height: 4px; background: {t.rule}; }}
QProgressBar#level::chunk {{ background: {t.ink2}; }}

QSlider::groove:horizontal {{ height: 2px; background: {t.rule2}; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; }}
QSlider::handle:horizontal {{ background: {t.ink}; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {{ background: {t.focus}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle {{ background: {t.rule2}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {t.rule}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {t.accent2}; }}

QStatusBar {{ background: {t.paper}; border-top: 1px solid {t.rule}; color: {t.muted}; }}
QStatusBar::item {{ border: 0; }}

QLabel#muted {{ color: {t.muted}; }}
QLabel#label {{ color: {t.muted}; font-weight: 500; font-size: {TEXT_SM}pt; }}
QLabel#h1 {{ font-family: "{t.font_display}"; font-weight: 300; font-size: 30pt; }}
QLabel#h2 {{ font-family: "{t.font_display}"; font-weight: 300; font-size: {TEXT_XL}pt; }}
QLabel#itemTitle {{ font-weight: 500; font-size: {TEXT_LG}pt; }}
QLabel#cueText {{ font-family: "{t.font_display}"; font-weight: 300; font-size: {TEXT_2XL}pt; color: {t.ink}; }}
QLabel#cueWho {{ font-weight: 500; font-size: {TEXT_SM}pt; }}
QLabel#promptText {{ font-family: "{t.font_display}"; font-weight: 300; font-size: {TEXT_3XL}pt; padding: 18px 4px; }}
QLabel#countdown {{ font-weight: 500; font-size: {TEXT_LG}pt; }}
QFrame#cue {{ background: {t.paper}; border-top: 1px solid {t.rule}; }}
QFrame#well {{ background: {t.paper3}; border-radius: {RADIUS_MD}px; }}
QFrame#stepCard {{ background: {t.paper2}; border-radius: {RADIUS_MD}px; }}
QWidget#videoArea {{ background: {t.paper}; }}
QWidget#transport {{ background: {t.paper}; }}
"""


def _check_svg(t: Theme) -> str:
    """Галочка для чекбоксов: QSS принимает картинку только файлом."""
    from ..core.settings import data_dir
    path = data_dir() / "theme" / f"check_{t.name}_{t.paper.strip('#')}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{t.paper}" stroke-width="3.2" stroke-linecap="square">'
                        f'<path d="M5 12.5l4.5 4.5L19 7.5"/></svg>', encoding="utf-8")
    return path.as_posix()


def apply_theme(app: QApplication, name: str) -> Theme:
    global _current
    load_fonts()
    t = THEMES.get(name, MIDNIGHT)
    _current = t
    app.setStyle("Fusion")
    app.setFont(body_font())
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(t.paper))
    pal.setColor(QPalette.WindowText, c(t.ink))
    pal.setColor(QPalette.Base, c(t.paper2))
    pal.setColor(QPalette.AlternateBase, c(t.paper3))
    pal.setColor(QPalette.Text, c(t.ink))
    pal.setColor(QPalette.Button, c(t.paper2))
    pal.setColor(QPalette.ButtonText, c(t.ink))
    pal.setColor(QPalette.ToolTipBase, c(t.paper3))
    pal.setColor(QPalette.ToolTipText, c(t.ink))
    pal.setColor(QPalette.Highlight, c(t.paper3))
    pal.setColor(QPalette.HighlightedText, c(t.ink))
    pal.setColor(QPalette.PlaceholderText, c(t.muted))
    pal.setColor(QPalette.Mid, c(t.rule))
    pal.setColor(QPalette.Midlight, c(t.paper3))
    pal.setColor(QPalette.Link, c(t.accent))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(t.muted))
    app.setPalette(pal)
    app.setStyleSheet(_qss(t))
    _icon_cache.clear()
    return t


# ============================================================================ иконки
# Собственный набор контурных иконок 24×24 (stroke = цвет токена).

_ICONS: dict[str, str] = {
    "new": '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 5h6l2 2h10v12H3z"/>',
    "save": '<path d="M4 4h12l4 4v12H4z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "subs": '<rect x="3" y="5" width="18" height="14"/><path d="M7 15h4M14 15h3M7 11h2M12 11h5"/>',
    "whisper": '<rect x="9" y="3" width="6" height="11"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'
               '<path d="M2 9v4M22 9v4"/>',
    "separate": '<path d="M12 3 3 8l9 5 9-5z"/><path d="m3 13 9 5 9-5"/><path d="M3 17.5 12 22l9-4.5" opacity=".5"/>',
    "synth": '<path d="M3 12h2M7 8v8M11 4v16M15 7v10M19 10v4M21 12h0"/>',
    "preview": '<path d="M4 4h16v16H4z"/><path d="M10 8.5v7l5.5-3.5z"/>',
    "export": '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M5 13v8h14v-8"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
    "play": '<path d="M7 4.5v15L19 12z" fill="currentColor"/>',
    "pause": '<rect x="6" y="5" width="4" height="14" fill="currentColor"/>'
             '<rect x="14" y="5" width="4" height="14" fill="currentColor"/>',
    "back": '<path d="M11 7v10l-7-5zM20 7v10l-7-5z" fill="currentColor"/>',
    "fwd": '<path d="M13 7v10l7-5zM4 7v10l7-5z" fill="currentColor"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/>',
    "record": '<rect x="6" y="6" width="12" height="12" rx="6" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" fill="currentColor"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 13.5a7 7 0 0 1 4 6.5"/>',
    "settings": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><rect x="14" y="4" width="4" height="4"/>'
                '<rect x="8" y="10" width="4" height="4"/><rect x="16" y="16" width="4" height="4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "volume": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/>',
    "mic": '<rect x="9" y="3" width="6" height="11"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "film": '<rect x="3" y="3" width="18" height="18"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M3 12h18"/>',
    "help": '<rect x="3" y="3" width="18" height="18"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01"/>',
    "headphones": '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><rect x="3" y="14" width="4" height="7"/>'
                  '<rect x="17" y="14" width="4" height="7"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}

_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def _render_svg(svg: str, px: int) -> QPixmap:
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    return pm


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """Иконка из набора, окрашенная в токен (по умолчанию — цвет текста)."""
    color = color or _current.ink
    key = (name, color, size)
    if key in _icon_cache:
        return _icon_cache[key]
    body = _ICONS.get(name, "")

    def svg_for(col: str) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                f'stroke="{col}" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter">'
                f'{body.replace("currentColor", col)}</svg>')

    ic = QIcon()
    for scale in (1, 2):
        pm = _render_svg(svg_for(color), size * scale)
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm, QIcon.Normal)
    pm = _render_svg(svg_for(_current.muted), size * 2)
    pm.setDevicePixelRatio(2)
    ic.addPixmap(pm, QIcon.Disabled)
    _icon_cache[key] = ic
    return ic


def app_icon() -> QIcon:
    """Значок: квадрат полночного синего, азурная звуковая волна над строкой реплики."""
    m = MIDNIGHT
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="10" fill="{m.paper}"/>'
           f'<g stroke="{m.accent}" stroke-width="4" stroke-linecap="square">'
           f'<path d="M14 26v8M22 18v24M30 12v36M38 20v20M46 24v12"/></g>'
           f'<path d="M14 54h36" stroke="{m.ink}" stroke-width="3"/></svg>')
    ic = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        ic.addPixmap(_render_svg(svg, s))
    return ic
