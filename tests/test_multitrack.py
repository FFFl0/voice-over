import os
import subprocess

import numpy as np
import soundfile as sf

from voiceover.core import ffmpeg
from voiceover.core.export import export_project
from voiceover.core.models import Line, Project


def make_mkv(path):
    ffmpeg.run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=f=300:duration=2",
        "-f", "lavfi", "-i", "sine=f=500:duration=2",
        "-f", "lavfi", "-i", "sine=f=700:duration=2",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac:a:2", "1",
        "-metadata:s:a:0", "language=jpn", "-metadata:s:a:0", "title=Японский",
        "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=English",
        "-metadata:s:a:2", "language=rus", str(path)])


def full_probe(path):
    out = subprocess.run([ffmpeg.find_ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    return out


def project(tmp_path, main=1):
    v = tmp_path / "ep.mkv"
    make_mkv(v)
    p = Project(video_path=str(v), duration=2.0, path=str(tmp_path / "ep.vop"), audio_stream=main)
    p.project_dir.mkdir()
    ffmpeg.extract_audio(str(v), str(p.project_dir / "original.wav"), main, 48000, 2.0)
    p.original_wav = "original.wav"
    ch = p.add_character("A")
    sf.write(p.project_dir / "r.wav", np.full(24000, 0.1, np.float32), 48000)
    p.lines.append(Line(0.5, 1.0, "раз", ch.id, rec_path="r.wav"))
    return p


def test_probe_labels(tmp_path):
    v = tmp_path / "x.mkv"
    make_mkv(v)
    a = ffmpeg.probe(str(v)).audio
    labels = [ffmpeg.track_label(x) for x in a]
    assert len(a) == 3 and labels[0].startswith("jpn · «Японский» · AAC")
    assert "mono" in labels[2] and labels[2].startswith("rus")


def test_export_keeps_all_tracks(tmp_path):
    p = project(tmp_path)
    out = tmp_path / "all.mkv"
    export_project(p, str(out), ffmpeg.ExportOptions(loudness=None))
    info = ffmpeg.probe(str(out))
    assert len(info.audio) == 4
    assert info.audio[0].title == "Многоголосая озвучка"
    assert [a.title for a in info.audio[1:]] == ["Японский", "English", "Оригинал (rus)"]
    assert [a.language for a in info.audio[1:]] == ["jpn", "eng", "rus"]
    lines = [ln for ln in full_probe(out).splitlines() if "Audio:" in ln]
    assert "(default)" in lines[0] and not any("(default)" in ln for ln in lines[1:])


def test_export_only_main(tmp_path):
    p = project(tmp_path, main=1)
    out = tmp_path / "main.mp4"
    export_project(p, str(out), ffmpeg.ExportOptions(loudness=None, container="mp4",
                                                     all_originals=False))
    info = ffmpeg.probe(str(out))
    assert len(info.audio) == 2 and info.audio[1].language == "eng"   # основная — №2


def test_export_with_intro_all_tracks(tmp_path):
    p = project(tmp_path)
    intro = tmp_path / "intro.mp4"
    ffmpeg.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:size=160x120:rate=25:duration=1",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", str(intro)])
    out = tmp_path / "intro.mkv"
    export_project(p, str(out), ffmpeg.ExportOptions(loudness=None, intro=str(intro)))
    info = ffmpeg.probe(str(out))
    assert len(info.audio) == 4 and abs(info.duration - 3.0) < 0.3


def test_player_track_menu():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from voiceover.ui.video_player import VideoPlayer
    pl = VideoPlayer()
    assert pl.sound.count() == 3 and pl.sound_mode() == "orig"
    pl.set_audio_tracks(["jpn", "eng", "rus"], main=1)
    assert pl.sound.count() == 5 and pl.orig_track() == 1
    assert "основная" in pl.sound.currentText()
    pl.sound.setCurrentIndex(2)
    assert pl.sound_mode() == "orig" and pl.orig_track() == 2
    pl.set_sound_mode("dub")
    assert pl.sound_mode() == "dub"
    pl.set_audio_tracks(["jpn", "eng", "rus"], main=1)   # выбор сохраняется
    assert pl.sound_mode() == "dub"
    del app
