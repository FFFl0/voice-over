import shutil

import numpy as np
import pytest
import soundfile as sf

from voiceover.core import ffmpeg
from voiceover.core.export import export_project, write_burn_subs
from voiceover.core.models import Line, Project


def _gen(args):
    ffmpeg.run_ffmpeg(args)


@pytest.fixture
def media(tmp_path):
    d = tmp_path / "it's a dir"           # пробел и апостроф в пути — для фильтра subtitles
    d.mkdir()
    video = d / "ep.mkv"
    _gen(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
          "-f", "lavfi", "-i", "sine=f=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest", str(video)])
    intro = d / "intro.mp4"
    _gen(["-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30:duration=1",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", str(intro)])
    logo = d / "logo.png"
    _gen(["-f", "lavfi", "-i", "color=c=white@0.5:size=64x32,format=rgba", "-frames:v", "1",
          str(logo)])
    return d, video, intro, logo


def _project(d, video):
    p = Project(video_path=str(video), duration=3.0, path=str(d / "ep.vop"))
    p.project_dir.mkdir()
    sf.write(p.project_dir / "original.wav", np.zeros((3 * 48000, 2), np.float32), 48000)
    p.original_wav = "original.wav"
    ch = p.add_character("A")
    sf.write(p.project_dir / "r.wav", (0.3 * np.sin(np.arange(48000) / 10)).astype(np.float32),
             48000)
    p.lines.append(Line(0.5, 1.5, "Привет, мир: тест", ch.id, rec_path="r.wav"))
    return p


def test_copy_export_unchanged(media):
    d, video, _, _ = media
    p = _project(d, video)
    out = d / "plain.mkv"
    export_project(p, str(out), ffmpeg.ExportOptions(loudness=None))
    info = ffmpeg.probe(str(out))
    assert len(info.audio) == 2 and abs(info.duration - 3.0) < 0.2
    assert not ffmpeg.ExportOptions().reencode


def test_logo_subs_intro(media):
    d, video, intro, logo = media
    if not ffmpeg.has_filter("subtitles"):
        pytest.skip("FFmpeg без libass")
    p = _project(d, video)
    opts = ffmpeg.ExportOptions(loudness=None, logo=str(logo), logo_pos="br", intro=str(intro),
                                burn_subs=write_burn_subs(p, str(p.project_dir / "burn.ass")))
    assert opts.reencode
    out = d / "branded.mkv"
    export_project(p, str(out), opts)
    info = ffmpeg.probe(str(out))
    assert info.video_size == (320, 240) and len(info.audio) == 2
    assert abs(info.duration - 4.0) < 0.3                    # 1 с заставки + 3 с фильма
    # кадр в начале — красная заставка
    frame = d / "f.png"
    _gen(["-ss", "0.3", "-i", str(out), "-frames:v", "1", str(frame)])
    assert frame.exists()


def test_logo_only_mp4(media):
    d, video, _, logo = media
    p = _project(d, video)
    out = d / "logo.mp4"
    export_project(p, str(out), ffmpeg.ExportOptions(loudness=None, container="mp4",
                                                     logo=str(logo)))
    info = ffmpeg.probe(str(out))
    assert info.video_size == (320, 240) and abs(info.duration - 3.0) < 0.3
