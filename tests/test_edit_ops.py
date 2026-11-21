import numpy as np
import soundfile as sf

from voiceover.core import mixer
from voiceover.core.edit_ops import merge_lines, split_line
from voiceover.core.models import Line, Project

SR = 48000


def make(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=10)
    p.project_dir.mkdir()
    sf.write(p.project_dir / "orig.wav", np.zeros((10 * SR, 2), np.float32), SR)
    p.original_wav = "orig.wav"
    ch = p.add_character("A")
    # запись 3 с, начинается на 0.2 с раньше реплики
    t = np.arange(3 * SR) / SR
    sf.write(p.project_dir / "r.wav", (0.3 * np.sin(2 * np.pi * 500 * t)).astype(np.float32), SR)
    ln = Line(1.0, 3.6, "Раз два три четыре", ch.id, rec_path="r.wav", rec_offset=-0.2)
    ln.takes = {"r.wav": -0.2}
    p.lines.append(ln)
    return p, ln


def test_split_keeps_sound_in_place(tmp_path):
    p, ln = make(tmp_path)
    p.mix.normalize_voices = False
    before = mixer.prepare_clips(p)[0]
    new = split_line(p, ln, 1.9)
    assert new and p.lines == [ln, new] and ln.end == 1.9 and new.start == 1.9
    assert ln.text == "Раз два" and new.text == "три четыре"
    assert new.rec_path == "r.wav" and abs(new.rec_offset - (0.8 - 1.9)) < 1e-9
    clips = mixer.prepare_clips(p)[0]
    a, b = clips
    # части вместе дают тот же звук на тех же местах
    assert abs(a.start - before[0].start) < 1e-6
    assert abs(a.end(SR) - 1.9) < 1e-3 and abs(b.start - 1.9) < 1e-3
    assert abs(b.end(SR) - before[0].end(SR)) < 1e-3
    assert split_line(p, ln, 1.05) is None            # слишком близко к краю


def test_split_without_recording_and_merge_back(tmp_path):
    p, ln = make(tmp_path)
    ln.rec_path = ""
    new = split_line(p, ln, 2.0)
    assert not new.rec_path
    merged, warn = merge_lines(p, [new, ln])
    assert merged is ln and not warn and len(p.lines) == 1
    assert ln.text == "Раз два три четыре" and (ln.start, ln.end) == (1.0, 3.6)


def test_merge_moves_recording_of_later_line(tmp_path):
    p, ln = make(tmp_path)
    a = Line(0.0, 0.8, "Эй,", ln.character_id)
    p.lines.insert(0, a)
    merged, warn = merge_lines(p, [a, ln])
    assert merged is a and a.rec_path == "r.wav" and not warn
    assert abs(a.start + a.rec_offset - 0.8) < 1e-9      # звук остался на 0.8 с фильма
    assert a.text == "Эй, Раз два три четыре" and a.takes["r.wav"] == a.rec_offset


def test_merge_rules(tmp_path):
    p, ln = make(tmp_path)
    other = p.add_character("B")
    b = Line(4, 5, "x", other.id)
    p.lines.append(b)
    m, warn = merge_lines(p, [ln, b])
    assert m is None and "одного персонажа" in warn
    c = Line(4, 5, "y", ln.character_id, rec_path="r.wav")
    p.lines.append(c)
    m, warn = merge_lines(p, [ln, c])
    assert m is ln and "перезаписать" in warn


def test_window_split_merge(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    p, ln = make(tmp_path)
    (tmp_path / "v.mp4").write_bytes(b"")
    p.video_path = str(tmp_path / "v.mp4")
    w = MainWindow(AppSettings())
    w.set_project(p)
    monkeypatch.setattr(w.player, "position", lambda: 1.9)
    w.split_current()
    assert len(p.lines) == 2 and w.current_line() is p.lines[1] and w.dirty
    w.flush_history()
    w.table.selectAll()
    w.merge_selected()
    assert len(p.lines) == 1 and p.lines[0].text == "Раз два три четыре"
    assert p.lines[0].trim_out == 0.0 and p.lines[0].rec_offset == -0.2   # запись снова целая
    w.flush_history()
    w.undo()
    assert len(w.project.lines) == 2
    w.dirty = False
    w.close()
    del app
