import sys
import types
import wave

import numpy as np
import pytest
import soundfile as sf

from voiceover.tts import piper as P
from voiceover.tts.base import get_engine

SR = 22050


class FakeVoice:
    """Вместо модели Piper — синус 200 Гц длиной 1 с × length_scale."""
    loads = []

    @classmethod
    def load(cls, path, **kw):
        cls.loads.append(path)
        return cls()

    def synthesize_wav(self, text, wf: wave.Wave_write, syn_config=None, **kw):
        n = int(SR * (syn_config.length_scale if syn_config else 1.0))
        x = (0.3 * np.sin(2 * np.pi * 200 * np.arange(n) / SR) * 32767).astype("<i2")
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(x.tobytes())


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    mod = types.SimpleNamespace(PiperVoice=FakeVoice,
                                SynthesisConfig=lambda length_scale=None: types.SimpleNamespace(
                                    length_scale=length_scale))
    monkeypatch.setitem(sys.modules, "piper", mod)
    monkeypatch.setattr(P, "download_voice", lambda vid: tmp_path / f"{vid}.onnx")
    FakeVoice.loads.clear()
    return P.PiperEngine()


def peak_freq(path):
    x, sr = sf.read(path)
    spec = np.abs(np.fft.rfft(x))
    return np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spec)], len(x) / sr


def test_registered():
    assert get_engine("piper").voices()[0].id == "ru_RU-dmitri-medium"


def test_synth_rate_and_pitch(fake_piper, tmp_path):
    out = fake_piper.synthesize("Привет", "ru_RU-irina-medium", str(tmp_path / "a"), rate=0, pitch=0)
    f, dur = peak_freq(out)
    assert out.endswith(".wav") and abs(f - 200) < 3 and abs(dur - 1.0) < 0.02
    out = fake_piper.synthesize("Привет", "ru_RU-irina-medium", str(tmp_path / "b"), rate=100, pitch=0)
    assert abs(peak_freq(out)[1] - 0.5) < 0.02            # +100% темп — вдвое короче
    out = fake_piper.synthesize("Привет", "ru_RU-irina-medium", str(tmp_path / "c"), rate=0, pitch=30)
    f, dur = peak_freq(out)
    assert abs(f - 240) < 6 and abs(dur - 1.0) < 0.03      # выше на 20%, длительность та же
    assert len(FakeVoice.loads) == 1                       # модель загружается один раз


def test_download_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "voices_dir", lambda: tmp_path)
    got = []

    class R:
        def __init__(self, url):
            self.url = url
            self.status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"data:" + self.url.encode()

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: (got.append(url), R(url))[1])
    model = P.download_voice("ru_RU-dmitri-medium")
    assert model.exists() and (tmp_path / "ru_RU-dmitri-medium.onnx.json").exists()
    assert got[1].startswith("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
                             "ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx")
    P.download_voice("ru_RU-dmitri-medium")
    assert len(got) == 2                                   # повторно не качает
