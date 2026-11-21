import numpy as np
import soundfile as sf

from voiceover.core.align import align_line
from voiceover.core.models import Line, Project

SR = 48000


def syllables(pattern, sr=SR, f=220.0):
    """Речь-заглушка: слоги (вкл) и паузы по маске 50 мс."""
    out = []
    t = np.arange(int(0.05 * sr)) / sr
    for on in pattern:
        out.append((0.4 * np.sin(2 * np.pi * f * t)).astype(np.float32) if on
                   else np.zeros(len(t), np.float32))
    return np.concatenate(out)


def make(tmp_path, true_shift=0.27, cur=0.0):
    p = Project(path=str(tmp_path / "p.vop"), duration=10)
    d = p.project_dir
    d.mkdir()
    rng = np.random.default_rng(3)
    pat = rng.random(40) > 0.45
    voice = syllables(pat)
    orig = np.zeros(10 * SR, np.float32)
    s = int((2.0 + true_shift) * SR)
    orig[s:s + len(voice)] = voice
    orig += rng.normal(0, 0.003, len(orig)).astype(np.float32)
    sf.write(d / "vocals.wav", orig, SR)
    rec = d / "rec"
    rec.mkdir()
    take = syllables(pat, f=330.0) * 0.6                       # другой голос, тот же ритм
    sf.write(rec / "a_1.wav", take, SR)
    ln = Line(2.0, 4.0, "фраза", rec_path="rec/a_1.wav", rec_offset=cur)
    p.lines.append(ln)
    return p, ln


def test_finds_shift(tmp_path):
    p, ln = make(tmp_path, 0.27)
    r = align_line(p, ln)
    assert r.applied and abs(ln.rec_offset - 0.27) <= 0.011 and r.score > 0.8
    assert ln.takes["rec/a_1.wav"] == ln.rec_offset
    r2 = align_line(p, ln)                       # повторно — уже на месте
    assert not r2.applied and abs(r2.shift) < 0.011


def test_negative_shift(tmp_path):
    p, ln = make(tmp_path, -0.18, cur=0.1)
    align_line(p, ln)
    assert abs(ln.rec_offset + 0.18) <= 0.011


def test_low_confidence_keeps_offset(tmp_path):
    p, ln = make(tmp_path)
    sf.write(p.project_dir / "vocals.wav", np.zeros(10 * SR, np.float32), SR)  # тишина
    r = align_line(p, ln)
    assert not r.applied and ln.rec_offset == 0.0


def test_no_recording(tmp_path):
    p, ln = make(tmp_path)
    ln.rec_path = ""
    assert align_line(p, ln) is None


def test_window_align_action(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    p, ln = make(tmp_path, 0.27)
    (p.project_dir / "v.mp4").write_bytes(b"")
    p.video_path = str(p.project_dir / "v.mp4")
    w = MainWindow(AppSettings())
    w.set_project(p)
    w.record.set_line(ln)
    w.align_recordings([ln.id], quiet=False)
    assert abs(ln.rec_offset - 0.27) <= 0.011
    assert abs(w.record.offset.value() - ln.rec_offset) < 1e-9 and w.dirty
    w.dirty = False
    w.close()
    del app
