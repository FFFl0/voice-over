import numpy as np
import soundfile as sf

from voiceover.core import diarize
from voiceover.core.models import Line, Project

SR = 16000


def voice(f0, formant, sec, seed):
    """Голос-заглушка: гармоники f0 с «формантой» и дрожанием тона."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * 4 * t + rng.random()))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum(np.sin(k * ph) * np.exp(-((k * f0 - formant) / 600) ** 2) / k for k in range(1, 30))
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    return (0.3 * x * env / np.abs(x).max()).astype(np.float32)


def make(tmp_path, labelled=True):
    p = Project(path=str(tmp_path / "p.vop"), duration=60)
    p.project_dir.mkdir()
    audio = np.zeros(60 * SR, np.float32)
    kinds = [(110, 700), (220, 1800), (330, 3000)]          # «мужчина», «женщина», «ребёнок»
    order = [0, 1, 2, 0, 1, 2, 1, 0, 2, 1, 0, 2]
    t = 1.0
    for i, k in enumerate(order):
        f0, fm = kinds[k]
        x = voice(f0, fm, 2.0, i)
        audio[int(t * SR): int(t * SR) + len(x)] = x
        p.lines.append(Line(t, t + 2.0, f"реплика {i}"))
        t += 3.0
    p.lines.append(Line(50.0, 50.3, "ой"))                   # слишком короткая
    sf.write(p.project_dir / "orig.wav", audio, SR)
    p.original_wav = "orig.wav"
    return p, order


def test_fbank_shape():
    f = diarize.fbank(np.zeros(SR, np.float32))
    assert f.shape == (98, 80) and np.isfinite(f).all()


def test_cluster():
    rng = np.random.default_rng(0)
    centers = rng.normal(size=(3, 16))
    emb = np.concatenate([c + 0.05 * rng.normal(size=(5, 16)) for c in centers])
    lab = diarize.cluster(emb, None, 0.3)
    assert len(set(lab)) == 3 and len(set(lab[:5])) == 1 and len(set(lab[5:10])) == 1
    assert len(set(diarize.cluster(emb, 2))) == 2


def test_assign_new_voices_mfcc(tmp_path):
    p, order = make(tmp_path)
    res = diarize.assign_roles(p, n_voices=3, model_dir="")
    assert not res.used_model and res.skipped_short == 1 and len(res.new_characters) == 3
    got = [ln.character_id for ln in p.lines[:12]]
    groups = {}
    for k, cid in zip(order, got):
        groups.setdefault(k, set()).add(cid)
    assert all(len(v) == 1 for v in groups.values()) and len({next(iter(v)) for v in groups.values()}) == 3


def test_labelled_lines_propagate(tmp_path):
    p, order = make(tmp_path)
    names = ["Какаши", "Сакура", "Наруто"]
    chars = [p.add_character(n) for n in names]
    for i in (0, 1, 2):                         # по одной размеченной реплике каждого
        p.lines[i].character_id = chars[order[i]].id
    res = diarize.assign_roles(p, n_voices=3, model_dir="")
    assert res.new_characters == [] and res.assigned == 9
    for ln, k in zip(p.lines[:12], order):
        assert ln.character_id == chars[k].id
    assert p.lines[12].character_id is None    # короткую не трогаем


def test_custom_embedder_marks_model(tmp_path):
    p, order = make(tmp_path)
    res = diarize.assign_roles(p, n_voices=None, embedder=lambda x: np.array([1.0, 0.0]))
    assert res.used_model and len(res.new_characters) == 1


def test_window_action(tmp_path, monkeypatch):
    import os
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    p, order = make(tmp_path)
    (p.project_dir / "v.mp4").write_bytes(b"")
    p.video_path = str(p.project_dir / "v.mp4")
    w = MainWindow(AppSettings())
    w.set_project(p)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    msgs = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: msgs.append(a[2]))
    monkeypatch.setattr(diarize, "load_model", lambda d: None)
    w.diarize_roles()
    t0 = time.time()
    while w.task is not None and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert msgs and "Назначено ролей" in msgs[0] and "простой способ" in msgs[0]
    assert all(ln.character_id for ln in p.lines[:12]) and w.dirty
    w.dirty = False
    w.close()
