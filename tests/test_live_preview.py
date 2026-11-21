import os
import time

import numpy as np
import soundfile as sf

from voiceover.core import mixer
from voiceover.core.models import Line, Project

SR = 48000


def test_preview_follows_take_edits(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui.main_window import MainWindow
    p = Project(video_path=str(tmp_path / "v.mp4"), duration=12, path=str(tmp_path / "p.vop"))
    (tmp_path / "v.mp4").write_bytes(b"")
    p.project_dir.mkdir()
    t = np.arange(12 * SR) / SR
    sf.write(p.project_dir / "orig.wav", np.stack([0.1 * np.sin(2 * np.pi * 100 * t)] * 2, 1), SR)
    p.original_wav = "orig.wav"
    ch = p.add_character("A")
    tt = np.arange(2 * SR) / SR
    sf.write(p.project_dir / "r.wav", (0.3 * np.sin(2 * np.pi * 700 * tt)).astype(np.float32), SR)
    ln = Line(3.0, 5.0, "раз", ch.id, rec_path="r.wav")
    p.lines.append(ln)
    w = MainWindow(AppSettings())
    w.set_project(p)
    w.build_preview(None, (0.0, 12.0))
    t0 = time.time()
    while w.task is not None and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    out = p.project_dir / "preview.wav"
    assert out.exists() and w.timeline.canvas.preview_range

    def wait_patch():
        t0 = time.time()
        while time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.02)
            if not w._patch_timer.isActive() and not getattr(w, "_patching", False) \
                    and w._patch_region is None:
                break
        app.processEvents()

    # правка дубля: сдвиг на 2 с позже и обрезка конца
    ln.rec_offset, ln.trim_out = 2.0, 0.5
    w._on_line_edited(ln.id, "Правка")
    wait_patch()
    got, _ = sf.read(out, dtype="float32")
    ref = tmp_path / "ref.wav"
    mixer.render_project(p, str(ref), t_from=0.0, t_to=12.0)
    want, _ = sf.read(ref, dtype="float32")
    assert got.shape == want.shape and np.max(np.abs(got - want)) < 1e-3
    # удаление реплики — превью (12 с) пересобирается целиком
    p.lines.clear()
    w.preview_invalidate()
    wait_patch()
    got, _ = sf.read(out, dtype="float32")
    mixer.render_project(p, str(ref), t_from=0.0, t_to=12.0)
    want, _ = sf.read(ref, dtype="float32")
    assert np.max(np.abs(got - want)) < 1e-3
    w.dirty = False
    w.close()
    del app
