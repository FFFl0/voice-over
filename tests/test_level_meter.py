import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_meter():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.ui.level_meter import FLOOR, LevelMeter, to_db
    m = LevelMeter()
    m.resize(300, 30)
    assert abs(to_db(0.5) + 6.02) < 0.01 and to_db(0) == FLOOR
    m.set_level(0.1)
    assert abs(m.db + 20) < 0.01 and not m.clipped
    m.set_level(0.999)
    assert m.clipped and m.peak_db > -0.1
    m.set_level(0.01)                       # сразу после — показания падают плавно
    assert m.db > -20
    assert not m.grab().isNull()
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    m.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(295, 8), QPointF(295, 8),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert not m.clipped
    del app
