import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


def test_paint_and_overlay():
    app = QApplication.instance() or QApplication([])
    from voiceover.ui.cue import CueOverlay, paint_cue
    img = QImage(400, 300, QImage.Format_ARGB32)
    for mode, v, e in (("prep", 2.5, 0.2), ("speak", 0, 0.3), ("tail", 0, 2), ("next", 0, 0), ("", 0, 0)):
        img.fill(QColor("black"))
        p = QPainter(img)
        paint_cue(p, QRectF(0, 0, 400, 300), mode, v, e)
        p.end()
        red = QColor(img.pixel(1, 280)).red()
        assert (red > 200) == (mode in ("prep", "speak", "tail"))   # красная рамка при записи
    win = QWidget()
    target = QWidget(win)
    target.setGeometry(10, 10, 300, 200)
    win.resize(400, 300)
    win.show()
    ov = CueOverlay(target, win)
    ov.set_state("prep", 2.0, 0.1)
    assert ov.isVisible() and ov.size() == target.size()
    ov.set_state("", 0, 0)
    assert not ov.isVisible()
    del app
