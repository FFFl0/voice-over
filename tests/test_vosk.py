import json
import sys
import types
import wave

import numpy as np
import pytest
import soundfile as sf

from voiceover.tts import vosk as V
from voiceover.tts.base import get_engine

SR = 22050


def test_pick_model_name():
    listing = [{"name": "vosk-model-small-ru-0.22", "lang": "ru", "obsolete": "false"},
               {"name": "vosk-model-tts-ru-0.7-multi", "obsolete": "false"},
               {"name": "vosk-model-tts-ru-0.12-multi", "obsolete": "false"},
               {"name": "vosk-model-tts-ru-0.6-multi", "obsolete": "true"}]
    names = V.pick_model_name(listing)
    assert names[0] == "vosk-model-tts-ru-0.12-multi"        # 0.12 новее 0.7, не по строке
    assert "vosk-model-tts-ru-0.6-multi" not in names         # устаревшая
    assert "vosk-model-small-ru-0.22" not in names            # не TTS
    assert names[1:4] == ["vosk-model-tts-ru-0.10-multi", "vosk-model-tts-ru-0.9-multi",
                          "vosk-model-tts-ru-0.8-multi"]
    assert V.pick_model_name(None)[0] == "vosk-model-tts-ru-0.10-multi"


def test_speaker_count():
    assert V.speaker_count({"num_speakers": 57}) == 57
    assert V.speaker_count({"data": {"n_speakers": 12}}) == 12
    assert V.speaker_count({"speaker_id_map": {"a": 0, "b": 1}}) == 2
    assert V.speaker_count({}) == 5


@pytest.fixture
def model_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(V, "models_dir", lambda: tmp_path)
    return tmp_path


def make_model(root, name, n):
    d = root / name
    d.mkdir()
    (d / "model.onnx").write_bytes(b"x")
    (d / "config.json").write_text(json.dumps({"num_speakers": n}))
    return d


def test_voices_before_and_after_download(model_dir):
    eng = V.VoskEngine()
    before = eng.voices()
    assert len(before) == 5 and before[0].gender == "F" and before[4].gender == "M"
    make_model(model_dir, "vosk-model-tts-ru-0.7-multi", 5)
    make_model(model_dir, "vosk-model-tts-ru-0.10-multi", 57)
    assert V.installed_model().name == "vosk-model-tts-ru-0.10-multi"
    after = eng.voices()
    assert len(after) == 57 and after[56].id == "56" and after[56].name == "Голос 57"


class FakeSynth:
    calls = []

    def __init__(self, model):
        self.model = model

    def synth(self, text, oname, speaker_id=0, speech_rate=1.0, **kw):
        FakeSynth.calls.append((text, speaker_id, speech_rate))
        n = int(SR / speech_rate)
        x = (0.3 * np.sin(2 * np.pi * 150 * np.arange(n) / SR) * 32767).astype("<i2")
        with wave.open(oname, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(x.tobytes())


def test_synthesize(monkeypatch, model_dir, tmp_path):
    make_model(model_dir, "vosk-model-tts-ru-0.10-multi", 57)
    monkeypatch.setitem(sys.modules, "vosk_tts",
                        types.SimpleNamespace(Model=lambda model_path: model_path, Synth=FakeSynth))
    FakeSynth.calls.clear()
    eng = get_engine("vosk").__class__()
    out = eng.synthesize("Привет", "12", str(tmp_path / "a"), rate=50, pitch=30)
    x, sr = sf.read(out)
    f = np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(np.abs(np.fft.rfft(x)))]
    assert FakeSynth.calls[0] == ("Привет", 12, 1.5)
    assert abs(len(x) / sr - 1 / 1.5) < 0.03 and abs(f - 180) < 6
    eng.synthesize("Ещё", "3", str(tmp_path / "b"))
    with pytest.raises(Exception):
        eng.synthesize("x", "не число", str(tmp_path / "c"))
