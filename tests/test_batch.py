import numpy as np
import soundfile as sf

from voiceover.core import batch, ffmpeg, separation
from voiceover.core.models import Line, MixMode, Project


def make_video(path):
    ffmpeg.run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                       "-f", "lavfi", "-i", "sine=f=300:duration=2", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])


def make_project(d, name, video_ok=True):
    v = d / f"{name}.mkv"
    if video_ok:
        make_video(v)
    p = Project(video_path=str(v), duration=2.0, path=str(d / f"{name}.vop"))
    p.project_dir.mkdir()
    ch = p.add_character("A")
    sf.write(p.project_dir / "r.wav", np.full(24000, 0.1, np.float32), 48000)
    p.lines.append(Line(0.5, 1.0, "раз", ch.id, rec_path="r.wav"))
    p.save()
    return p


def test_batch(tmp_path, monkeypatch):
    good = make_project(tmp_path, "ep01")
    dub = make_project(tmp_path, "ep02")
    dub.mix.mode = MixMode.DUBBING
    dub.save()
    bad = make_project(tmp_path, "ep03", video_ok=False)

    def fake_sep(orig, bg, vocals, *a, **k):
        x, sr = sf.read(orig)
        sf.write(bg, x * 0.5, sr)
        sf.write(vocals, x * 0.5, sr)
    monkeypatch.setattr(separation, "separate_vocals", fake_sep)
    seen = []
    res = batch.run_batch([good.path, dub.path, bad.path], {"extract", "separate", "export"},
                          ffmpeg.ExportOptions(loudness=None), progress=lambda f, m: seen.append(f))
    assert [r.ok for r in res] == [True, True, False] and "видео" in res[2].message
    assert (tmp_path / "ep01.voiceover.mkv").exists() and (tmp_path / "ep02.voiceover.mkv").exists()
    assert "голоса отделены" in res[1].done and Project.load(dub.path).background_wav
    assert seen == sorted(seen) and seen[-1] == 1.0
    # повторный запуск: всё готово — видео не пересобирается
    res2 = batch.run_batch([good.path], {"extract", "separate", "export"},
                           ffmpeg.ExportOptions(loudness=None))
    assert res2[0].done == ["видео уже есть — пропущено"]


def test_dialog(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.core.settings import AppSettings
    from voiceover.ui import batch_dialog
    from voiceover.ui.main_window import MainWindow
    p = make_project(tmp_path, "ep01")
    w = MainWindow(AppSettings())
    w.set_project(Project.load(p.path))
    dlg = batch_dialog.BatchDialog(w)
    assert dlg.paths() == [p.path] and dlg.steps["export"].isChecked()
    started = []
    monkeypatch.setattr(w, "run_task", lambda *a, **k: started.append(a))
    dlg._start()
    assert started and started[0][0].title == "Пакетная обработка"
    w.dirty = False
    w.close()
    del app
