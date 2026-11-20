import numpy as np
import soundfile as sf

from voiceover.core import mixer
from voiceover.core.models import Line, MixMode, Project

SR = 48000


def tone(sec, freq=440, amp=0.3, sr=SR):
    t = np.arange(int(sec * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def make_project(tmp_path):
    p = Project(path=str(tmp_path / "p.vop"), duration=6.0)
    d = p.project_dir
    d.mkdir()
    bg = np.stack([tone(6, 100, 0.2)] * 2, axis=1)
    sf.write(d / "orig.wav", bg, SR, subtype="PCM_16")
    p.original_wav = "orig.wav"
    ch = p.add_character("A")
    # запись актёра 2 с с тишиной по краям; следующая реплика без записи
    voice = np.concatenate([np.zeros(SR // 4, np.float32), tone(1.5, 800, 0.5),
                            np.zeros(SR // 4, np.float32)])
    sf.write(d / "v.wav", voice, SR)
    ln = Line(1.0, 2.0, "раз", ch.id, rec_path="v.wav")
    ln2 = Line(2.2, 3.0, "два", ch.id)  # без звука — предупреждение
    p.lines += [ln, ln2]
    return p


def test_trim_silence():
    x = np.concatenate([np.zeros(SR), tone(1), np.zeros(SR)])
    y = mixer.trim_silence(x, SR)
    assert abs(len(y) / SR - 1.06) < 0.03


def test_soft_limit():
    x = np.array([0.5, 0.95, 3.0, -5.0], np.float32)
    y = mixer.soft_limit(x)
    assert y[0] == 0.5 and np.all(np.abs(y) <= 1.0) and y[3] < -0.89


def test_render_voiceover(tmp_path):
    p = make_project(tmp_path)
    out = tmp_path / "mix.wav"
    warnings = mixer.render_project(p, str(out))
    assert any("нет записи" in w for w in warnings)
    y, sr = sf.read(out)
    assert sr == SR and y.shape[1] == 2 and abs(len(y) / SR - 6.0) < 0.01
    clips, _ = mixer.prepare_clips(p)
    assert len(clips) == 1
    c = clips[0]
    # запись идёт в микс целиком и с начала реплики: без обрезки и ускорения
    assert abs(c.length / SR - 2.0) < 0.01 and c.start == 1.0
    # Фон приглушён под репликой: сравним уровень 100 Гц до и во время реплики
    def band_level(seg, f):
        spec = np.abs(np.fft.rfft(seg[:, 0]))
        freqs = np.fft.rfftfreq(len(seg), 1 / SR)
        return spec[np.argmin(np.abs(freqs - f))]
    before = band_level(y[int(0.2 * SR): int(0.7 * SR)], 100)
    during = band_level(y[int(1.3 * SR): int(1.8 * SR)], 100)
    assert during < before * 0.4  # -12 дБ ≈ 0.25
    # Голос присутствует
    assert band_level(y[int(1.3 * SR): int(1.8 * SR)], 800) > during * 2


def test_render_dubbing_requires_background(tmp_path):
    p = make_project(tmp_path)
    p.mix.mode = MixMode.DUBBING
    try:
        mixer.render_project(p, str(tmp_path / "m.wav"))
    except RuntimeError as e:
        assert "Отделить голоса" in str(e)
    else:
        raise AssertionError("ожидалась ошибка")
    p.background_wav = "orig.wav"
    mixer.render_project(p, str(tmp_path / "m.wav"))


def test_range_render(tmp_path):
    p = make_project(tmp_path)
    out = tmp_path / "part.wav"
    mixer.render_project(p, str(out), t_from=1.0, t_to=3.0)
    assert abs(sf.info(str(out)).duration - 2.0) < 0.01


def test_long_render_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(mixer, "CHUNK_SEC", 0.7)
    p = make_project(tmp_path)
    a = tmp_path / "a.wav"
    mixer.render_project(p, str(a))
    monkeypatch.setattr(mixer, "CHUNK_SEC", 20.0)
    b = tmp_path / "b.wav"
    mixer.render_project(p, str(b))
    ya, _ = sf.read(a)
    yb, _ = sf.read(b)
    assert np.max(np.abs(ya - yb)) < 1e-4


def test_peaks(tmp_path):
    from voiceover.core.waveform import compute_peaks
    x = np.concatenate([np.zeros(SR), tone(1.0, amp=0.5)])
    sf.write(tmp_path / "w.wav", x, SR)
    pk = compute_peaks(str(tmp_path / "w.wav"), 50, tmp_path / "c")
    assert len(pk) == 100 and pk[:50].max() == 0 and abs(float(pk[60]) - 0.5) < 0.01
    assert list((tmp_path / "c").glob("*.npy"))
    assert np.array_equal(compute_peaks(str(tmp_path / "w.wav"), 50, tmp_path / "c"), pk)


def test_loudness_normalization(tmp_path):
    from voiceover.core import ffmpeg
    t = np.arange(SR * 6) / SR
    quiet = (0.02 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)   # около −37 LUFS
    src = tmp_path / "quiet.wav"
    sf.write(src, np.stack([quiet, quiet], 1), SR)
    before = ffmpeg.measure_loudness(str(src))
    assert float(before["input_i"]) < -30
    for target in (-23.0, -16.0):
        out = tmp_path / f"norm{int(target)}.wav"
        ffmpeg.normalize_loudness(str(src), str(out), target)
        after = ffmpeg.measure_loudness(str(out), target)
        assert abs(float(after["input_i"]) - target) < 1.0, after
        assert float(after["input_tp"]) <= -0.5          # пик не выше −1 dBTP (с запасом)
        assert abs(sf.info(str(out)).duration - 6.0) < 0.05
    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros((SR, 2), np.float32), SR)
    ffmpeg.normalize_loudness(str(silent), str(tmp_path / "s.wav"), -23.0)   # без ошибки


def test_dubbing_keeps_original_outside_lines(tmp_path):
    """Demucs «съел» весь звук: вне реплик должен звучать оригинал, под репликами — фон."""
    p = make_project(tmp_path)
    p.mix.mode = MixMode.DUBBING
    sf.write(p.project_dir / "bg.wav", np.zeros((6 * SR, 2), np.float32), SR)
    p.background_wav = "bg.wav"
    out = tmp_path / "dub.wav"
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    def orig_level(a, b):   # уровень тона оригинала 100 Гц (голос записи — 800 Гц)
        seg = y[int(a * SR): int(b * SR), 0]
        spec = np.abs(np.fft.rfft(seg)) * 2 / len(seg)
        return float(spec[np.argmin(np.abs(np.fft.rfftfreq(len(seg), 1 / SR) - 100))])
    assert orig_level(4.0, 5.5) > 0.15   # реплик нет — оригинал (тон 0.2) на месте
    assert orig_level(1.3, 2.8) < 1e-3   # под репликами — только фон без голосов
    # без галочки — как раньше: только фон
    p.mix.dub_only_lines = False
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert float(np.abs(y[4 * SR: 5 * SR]).max()) < 1e-3


def test_old_mix_settings_default_only_lines():
    from voiceover.core.models import MixSettings
    m = MixSettings.from_dict({"mode": "dubbing"})
    assert m.dub_only_lines and MixSettings.from_dict(m.to_dict()).dub_only_lines


def _with_stems(tmp_path):
    """Проект после «Отделить голоса»: фон — тон 100 Гц, голоса оригинала — 300 Гц."""
    p = make_project(tmp_path)
    d = p.project_dir
    sf.write(d / "background.wav", np.stack([tone(6, 100, 0.2)] * 2, 1), SR)
    sf.write(d / "vocals.wav", np.stack([tone(6, 300, 0.2)] * 2, 1), SR)
    p.background_wav = "background.wav"
    return p


def _level(y, a, b, f):
    seg = y[int(a * SR): int(b * SR), 0]
    spec = np.abs(np.fft.rfft(seg)) * 2 / len(seg)
    return float(spec[np.argmin(np.abs(np.fft.rfftfreq(len(seg), 1 / SR) - f))])


def test_stems_mute(tmp_path):
    p = _with_stems(tmp_path)
    assert mixer.wave_keys(p) == ["vocals", "background"]
    out = tmp_path / "o.wav"
    # закадровый: вне реплик слышны и фон, и голоса оригинала
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 4, 5.5, 100) > 0.15 and _level(y, 4, 5.5, 300) > 0.15
    p.mix.muted = ["vocals"]
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 4, 5.5, 100) > 0.15 and _level(y, 4, 5.5, 300) < 1e-3
    p.mix.muted = ["background"]
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 4, 5.5, 100) < 1e-3 and _level(y, 4, 5.5, 300) > 0.15


def test_stems_dubbing(tmp_path):
    p = _with_stems(tmp_path)
    p.mix.mode = MixMode.DUBBING
    out = tmp_path / "o.wav"
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 1.3, 2.8, 300) < 1e-3          # под репликами голоса оригинала убраны
    assert _level(y, 1.3, 2.8, 100) > 0.15           # фон остаётся
    assert _level(y, 4, 5.5, 300) > 0.15             # вне реплик — оригинал целиком
    p.mix.muted = ["vocals"]
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 4, 5.5, 300) < 1e-3


def test_original_mute_without_stems(tmp_path):
    p = make_project(tmp_path)
    assert mixer.wave_keys(p) == ["original"]
    p.mix.muted = ["original"]
    out = tmp_path / "o.wav"
    mixer.render_project(p, str(out))
    y, _ = sf.read(out)
    assert _level(y, 4, 5.5, 100) < 1e-3
