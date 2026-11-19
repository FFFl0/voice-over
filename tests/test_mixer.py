import numpy as np
import soundfile as sf

from voiceover.core import mixer
from voiceover.core.models import Line, MixMode, Project, tts_hash

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
    # «синтез» длиной 2 с с тишиной по краям; слот реплики — 1 с, следующая через 1.2 с
    voice = np.concatenate([np.zeros(SR // 4, np.float32), tone(1.5, 800, 0.5),
                            np.zeros(SR // 4, np.float32)])
    sf.write(d / "v.wav", voice, SR)
    ln = Line(1.0, 2.0, "раз", ch.id, tts_path="v.wav", tts_hash=tts_hash("раз", ch.tts_params()))
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
    assert any("нет звука" in w for w in warnings)
    y, sr = sf.read(out)
    assert sr == SR and y.shape[1] == 2 and abs(len(y) / SR - 6.0) < 0.01
    clips, _ = mixer.prepare_clips(p)
    assert len(clips) == 1
    c = clips[0]
    # 1.56 с речи в окне 1.15 с -> ускорено, но не больше max_speedup
    assert 1.01 < c.tempo <= p.mix.max_speedup
    assert c.length / SR < 1.56
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
