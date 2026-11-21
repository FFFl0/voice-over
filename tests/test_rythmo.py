from voiceover.core.models import Line
from voiceover.core.rythmo import spoken_text, syllables, word_times, words_to_fractions


def test_syllables():
    assert syllables("Наруто") == 3 and syllables("в") == 1 and syllables("—") == 0
    assert syllables("こんにちは") == 5


def test_distribution_by_syllables():
    ln = Line(10.0, 13.0, "Я стану Хокаге!")
    w = word_times(ln)
    assert [x[2] for x in w] == ["Я", "стану", "Хокаге!"]
    assert w[0][0] == 10.0 and abs(w[-1][1] - 13.0) < 1e-6
    # длиннее слово — дольше звучит
    assert (w[2][1] - w[2][0]) > (w[1][1] - w[1][0]) > (w[0][1] - w[0][0])


def test_pause_after_comma():
    w = word_times(Line(0.0, 2.0, "Да, нет"))
    assert w[1][0] - w[0][1] > 0.1


def test_notes_are_not_spoken():
    assert spoken_text("(смеётся) Ну ты даёшь [шёпотом]") == "Ну ты даёшь"
    assert [x[2] for x in word_times(Line(0, 2, "(смеётся) Ну даёшь"))] == ["Ну", "даёшь"]


def test_whisper_words_used_and_follow_line():
    words = words_to_fractions(5.0, 7.0, [[5.0, 5.4, "Привет,"], [6.0, 6.8, "мир"]])
    ln = Line(5.0, 7.0, "Привет, мир", words=words)
    assert word_times(ln)[1][0] == 6.0
    ln.start, ln.end = 10.0, 14.0          # реплику передвинули и растянули
    assert abs(word_times(ln)[1][0] - 12.0) < 1e-6
    ln.text = "Здравствуй, мир"           # текст изменили — тайминг по слогам
    assert word_times(ln)[0][2] == "Здравствуй,"


def test_old_project_without_words():
    ln = Line.from_dict({"start": 0, "end": 1, "text": "раз два"})
    assert ln.words == [] and len(word_times(ln)) == 2


def test_band_widget(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.models import Project
    from voiceover.ui.rythmo import CUE_X, RythmoBand
    p = Project(duration=20)
    a, b = p.add_character("A"), p.add_character("B")
    p.lines += [Line(1.0, 3.0, "Привет, мир", a.id), Line(2.0, 4.0, "Одновременно", b.id)]
    band = RythmoBand()
    band.resize(800, 90)
    band.set_project(p)
    band.set_time(2.5)
    img = band.grab()
    assert not img.isNull()
    seeks = []
    band.seekRequested.connect(seeks.append)
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    x = 800 * CUE_X + band.pps   # на секунду правее линии
    band.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(x, 20), QPointF(x, 20),
                                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert abs(seeks[0] - 3.5) < 1e-6
    band.set_playing(True)
    assert band.now() >= 2.5
    band.set_playing(False)
    del app


def test_speech_rate():
    from voiceover.core.rythmo import rate_level, speech_rate
    assert abs(speech_rate(Line(0, 1, "Я стану Хокаге!")) - 6.0) < 1e-6
    assert rate_level(speech_rate(Line(0, 1, "Я стану Хокаге!"))) == "ok"
    long_ru = Line(0, 1, "Я обязательно стану самым сильным")
    assert rate_level(speech_rate(long_ru)) == "too_fast"
    assert speech_rate(Line(0, 1, "(смеётся)")) == 0.0
