import numpy as np
import pytest
import soundfile as sf

from voiceover.core import ffmpeg, fx, mixer
from voiceover.core.models import Line, Project

SR = 48000


def tone(sec, f=440, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def test_denoise():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.02, SR * 3).astype(np.float32)
    x = noise.copy()
    x[SR: 2 * SR] += tone(1.0)
    y = fx.denoise(x, SR, 0.6)
    assert len(y) == len(x)
    assert rms(y[: SR // 2]) < rms(x[: SR // 2]) * 0.3          # шум в паузе подавлен
    assert abs(rms(y[int(1.2 * SR): int(1.8 * SR)]) / rms(tone(0.6)) - 1) < 0.15   # голос цел
    assert fx.denoise(x, SR, 0.0) is x


def test_highpass_removes_hum():
    x = tone(2, 50, 0.3) + tone(2, 1000, 0.3)
    y = fx.denoise(x, SR, 0.3)
    spec = np.abs(np.fft.rfft(y[SR // 2: SR + SR // 2]))
    f = np.fft.rfftfreq(SR, 1 / SR)
    assert spec[np.argmin(abs(f - 50))] < spec[np.argmin(abs(f - 1000))] * 0.2


def test_match_eq_moves_towards_reference():
    rng = np.random.default_rng(1)
    white = rng.normal(0, 0.1, SR * 2).astype(np.float32)
    dark = np.convolve(white, np.ones(8) / 8, mode="same").astype(np.float32)   # глухая запись
    y = fx.match_eq(dark, white, SR, max_db=6)
    c, a = fx._band_profile(dark, SR)
    _, b = fx._band_profile(y, SR)
    hi = c > 4000
    assert (b[hi] - a[hi]).mean() > 3                                   # стало ярче
    assert abs(rms(y) / rms(dark) - 1) < 0.5


@pytest.mark.parametrize("key", [k for k in fx.EFFECTS if k])
def test_effect_filters_run(tmp_path, key):
    src = tmp_path / "in.wav"
    sf.write(src, tone(1.0), SR)
    out = tmp_path / "out.wav"
    ffmpeg.convert_audio(str(src), str(out), SR, filters=fx.effect_filter(key, True))
    y, sr = sf.read(out)
    assert sr == SR and len(y) > SR * 0.8 and np.isfinite(y).all() and np.abs(y).max() > 0.01


def test_mixer_applies_processing(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=4)
    d = p.project_dir
    d.mkdir()
    sf.write(d / "orig.wav", np.zeros((4 * SR, 2), np.float32), SR)
    p.original_wav = "orig.wav"
    ch = p.add_character("A", effect="phone")
    sf.write(d / "v.wav", tone(1.0, 100) + tone(1.0, 1000), SR)
    p.lines.append(Line(1, 2, "раз", ch.id, rec_path="v.wav"))
    clips, _ = mixer.prepare_clips(p)
    y, _ = sf.read(clips[0].path)
    spec = np.abs(np.fft.rfft(y[: SR // 2]))
    f = np.fft.rfftfreq(SR // 2, 1 / SR)
    assert spec[np.argmin(abs(f - 100))] < spec[np.argmin(abs(f - 1000))] * 0.1   # телефон режет низ
    first = clips[0].path
    ch.effect = ""
    p.mix.denoise = 0.5
    clips2, _ = mixer.prepare_clips(p)
    assert clips2[0].path != first                       # настройки входят в ключ кэша
