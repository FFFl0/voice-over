import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from voiceover.core import ocr
from voiceover.core.subtitles import SubLine

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def frame_with_text(seed: int, text: bool) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = (rng.random((60, 200, 3)) * 120).astype(np.uint8)     # шумный тёмный фон
    if text:
        x0 = 20 + seed % 3 * 40
        img[20:40, x0:x0 + 60] = 0                                 # обводка
        img[24:36, x0 + 4:x0 + 56] = 255                           # белые «буквы»
    return img


def test_segments_split_on_change():
    s = ocr.OcrSettings(fps=4, min_duration=0.4)
    seq = []
    t = 0.0
    for key, n in ((None, 4), (0, 8), (None, 2), (1, 8), (2, 8)):
        for _ in range(n):
            seq.append((t, frame_with_text(key or 0, key is not None)))
            t += 0.25
    segs = ocr.detect_segments(iter(seq), s)
    assert [(round(x.start, 2), round(x.end, 2)) for x in segs] == [(1.0, 3.0), (3.5, 5.5), (5.5, 7.5)]


def test_merge_lines():
    out = ocr.merge_lines([SubLine(0, 1, "Hello there"), SubLine(1.1, 2, "Hello there!"),
                           SubLine(3, 4, "Other")])
    assert [(x.start, x.end, x.text) for x in out] == [(0, 2, "Hello there!"), (3, 4, "Other")]


def test_boxes_to_text_two_lines():
    items = [(50, 10, "world", 0.9), (10, 80, "there", 0.9), (12, 5, "Hello", 0.9),
             (52, 90, "noise", 0.2)]
    assert ocr.boxes_to_text(items) == "Hello there world"


@pytest.mark.skipif(not ocr.engines_available()["rapidocr"][0] or not Path(FONT).exists()
                    or not shutil.which("ffmpeg"), reason="нужны rapidocr, шрифт и ffmpeg")
def test_extract_from_video(tmp_path):
    video = tmp_path / "hard.mp4"
    dt = (f"drawtext=fontfile={FONT}:fontsize=46:fontcolor=white:borderw=3:bordercolor=black:"
          "x=(w-tw)/2:y=h-110")
    vf = (f"{dt}:text='Hello, how are you?':enable='between(t,1,3)',"
          f"{dt}:text='See you tomorrow':enable='between(t,4,6)'")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=25", "-t", "7", "-vf", vf, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    lines = ocr.extract_subtitles(str(video), ocr.OcrSettings(language="en"))
    assert len(lines) == 2
    assert abs(lines[0].start - 1.0) < 0.3 and abs(lines[0].end - 3.0) < 0.4
    # OCR может ошибиться в отдельной букве — проверяем похожесть, а не точное совпадение
    assert ocr.similar(lines[0].text, "Hello, how are you?") >= 0.8
    assert ocr.similar(lines[1].text, "See you tomorrow") >= 0.8
