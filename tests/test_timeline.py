"""Таймлайн: перетаскивание у края прокручивает его, кнопки заглушения дорожек."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voiceover.core.models import Line, Project  # noqa: E402
from voiceover.ui import timeline as tl  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def mouse(kind, x, y, buttons=Qt.LeftButton):
    btn = Qt.LeftButton if kind != QMouseEvent.MouseMove else Qt.NoButton
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), btn, buttons, Qt.NoModifier)


_keep = []


def make(app):
    p = Project(duration=600.0)
    ch = p.add_character("A")
    p.lines.append(Line(5.0, 7.0, "раз", ch.id))
    t = tl.Timeline()
    t.resize(800, 300)
    t.set_project(p)
    t.show()
    _keep.append(t)   # иначе виджет удалит сборщик мусора
    app.processEvents()
    return t, t.canvas, p


def drive(app, c, ticks=30):
    for _ in range(ticks):
        c._autoscroll_tick()
    app.processEvents()


def test_playhead_drag_scrolls(app):
    t, c, _ = make(app)
    seeks = []
    c.seekRequested.connect(seeks.append)
    y = tl.RULER_H / 2
    c.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, 300, y))
    c.mouseMoveEvent(mouse(QMouseEvent.MouseMove, c.width() + 40, y))   # за правым краем
    assert c._autoscroll.isActive()
    drive(app, c)
    assert c.offset > 1.0                        # таймлайн уехал вправо
    assert seeks[-1] > c.offset + c.visible_seconds() * 0.9   # курсор у правого края
    assert t.scroll.value() == int(c.offset * 10)
    off = c.offset
    c.mouseMoveEvent(mouse(QMouseEvent.MouseMove, tl.LABEL_W - 30, y))   # к левому краю
    drive(app, c)
    assert c.offset < off
    c.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, tl.LABEL_W, y, Qt.NoButton))
    assert not c._autoscroll.isActive()


def test_scroll_stops_at_end(app):
    _, c, _ = make(app)
    y = tl.RULER_H / 2
    c.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, 300, y))
    c.mouseMoveEvent(mouse(QMouseEvent.MouseMove, c.width() + 200, y))
    drive(app, c, 5000)
    assert abs(c.offset - (600 - c.visible_seconds())) < 1e-6
    assert c.playhead <= 600


def test_block_drag_follows_scroll(app):
    _, c, p = make(app)
    ln = p.lines[0]
    r = c.line_rect(ln, 0)
    y = r.center().y()
    c.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, r.center().x(), y))
    c.mouseMoveEvent(mouse(QMouseEvent.MouseMove, c.width() - 5, y))
    drive(app, c)
    assert c.offset > 0.5
    # блок под курсором: его середина — там же, где мышь
    assert abs(c.x_of((ln.start + ln.end) / 2) - (c.width() - 5)) < 2
    assert abs((ln.end - ln.start) - 2.0) < 1e-6
    edited = []
    c.lineEdited.connect(edited.append)
    c.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, c.width() - 5, y, Qt.NoButton))
    assert edited == [ln.id]


def test_wave_lanes_and_mute(app):
    _, c, _ = make(app)
    c.set_waves(["vocals", "background"])
    assert c.lanes_top == tl.RULER_H + 2 * tl.WAVE_H
    got = []
    c.muteToggled.connect(got.append)
    mr = c.mute_rect(1)
    c.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, mr.center().x(), mr.center().y()))
    assert got == ["background"] and c._drag is None
    c.set_muted({"background"})
    c.grab()   # отрисовка без ошибок
