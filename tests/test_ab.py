import numpy as np
import pytest
import soundfile as sf

from voiceover.core.ab import GAP, build_ab
from voiceover.core.models import Line, Project

SR = 48000


def tone(sec, f, amp):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def level(x, f):
    spec = np.abs(np.fft.rfft(x)) * 2 / len(x)
    return float(spec[np.argmin(np.abs(np.fft.rfftfreq(len(x), 1 / SR) - f))])


def test_ab(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=6)
    p.project_dir.mkdir()
    orig = np.zeros(6 * SR, np.float32)
    orig[2 * SR:3 * SR] = tone(1.0, 300, 0.05)          # тихий голос оригинала
    sf.write(p.project_dir / "vocals.wav", np.stack([orig, orig], 1), SR)
    sf.write(p.project_dir / "r.wav", tone(1.0, 800, 0.5), SR)
    p.mix.normalize_voices = False
    ln = Line(2.0, 3.0, "раз", None, rec_path="r.wav")
    p.lines.append(ln)
    out = tmp_path / "ab.wav"
    ta, tb = build_ab(p, ln, str(out))
    assert abs(ta - 1.6) < 0.01 and abs(tb - 1.6) < 0.01
    y, _ = sf.read(out, dtype="float32")
    y = y[:, 0]
    a, b = y[int(0.4 * SR):int(1.2 * SR)], y[int((ta + GAP + 0.4) * SR):int((ta + GAP + 1.2) * SR)]
    assert level(a, 300) > 0.1 and level(a, 800) < 1e-3       # A — оригинал
    assert level(b, 800) > 0.1 and level(b, 300) < 1e-3       # B — дубль
    assert abs(level(a, 300) / level(b, 800) - 1) < 0.1       # выровнены по громкости
    ln.rec_path = ""
    with pytest.raises(RuntimeError):
        build_ab(p, ln, str(out))
