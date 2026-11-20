"""Видеокарта есть, а библиотек CUDA нет: Whisper и перевод должны досчитать на процессоре."""
import sys
import types
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from voiceover.core import gpu, transcribe as tr

CUDA_ERR = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


def test_is_cuda_error():
    assert gpu.is_cuda_error(CUDA_ERR)
    assert gpu.is_cuda_error(RuntimeError("Could not load library cudnn_ops64_9.dll"))
    assert not gpu.is_cuda_error(RuntimeError("file not found: audio.wav"))


class GpuModel:
    def transcribe(self, audio, **kw):
        def gen():
            raise CUDA_ERR                       # падает при работе, а не при загрузке
            yield
        return gen(), SimpleNamespace(language="ja")


class CpuModel:
    def transcribe(self, audio, **kw):
        seg = SimpleNamespace(start=0.5, end=1.5, text=" こんにちは。", no_speech_prob=0, avg_logprob=-0.1,
                              words=[SimpleNamespace(start=0.5, end=1.5, word=" こんにちは。")])
        return iter([seg]), SimpleNamespace(language="ja")


def test_whisper_falls_back_to_cpu(tmp_path, monkeypatch):
    wav = tmp_path / "a.wav"
    sf.write(wav, np.zeros(48000 * 3, np.float32), 48000)
    loads = []

    def fake_load(name, device, root):
        loads.append(device)
        return GpuModel() if device == "auto" else CpuModel()

    monkeypatch.setattr(tr, "_load_model", fake_load)
    monkeypatch.setattr(tr, "whisper_available", lambda: (True, ""))
    msgs = []
    lines, lang = tr.transcribe(str(wav), "small", "ja", "auto",
                                progress=lambda f, m: msgs.append(m))
    assert loads == ["auto", "cpu"] and lang == "ja" and lines[0].text == "こんにちは。"
    assert any("процессоре" in m for m in msgs)


def test_nllb_falls_back_to_cpu(monkeypatch, tmp_path):
    from voiceover.core import translate as T
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_translate import FakeCT2, FakeSP

    class GpuCT2(FakeCT2):
        def translate_batch(self, *a, **k):
            if self.device == "cuda":
                raise CUDA_ERR
            return super().translate_batch(*a, **k)

    monkeypatch.setitem(sys.modules, "ctranslate2",
                        types.SimpleNamespace(Translator=GpuCT2, get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "sentencepiece",
                        types.SimpleNamespace(SentencePieceProcessor=FakeSP))
    monkeypatch.setattr(T, "ensure_nllb", lambda size, progress=None: tmp_path)
    monkeypatch.setattr(gpu, "cuda_usable", lambda: True)
    t = T.NllbLocal("en", "ru")
    assert t.model.device == "cuda"
    assert t.translate_batch(["hello"]) == ["HELLO"]
    assert t.model.device == "cpu"


def test_cuda_usable_without_card(monkeypatch):
    gpu.cuda_usable.cache_clear()
    monkeypatch.setitem(sys.modules, "ctranslate2",
                        types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    assert gpu.cuda_usable() is False
    gpu.cuda_usable.cache_clear()
    monkeypatch.setitem(sys.modules, "ctranslate2",
                        types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    assert gpu.cuda_usable() is False          # карта «есть», а libcublas в контейнере нет
    gpu.cuda_usable.cache_clear()


def test_separation_has_no_torchaudio():
    src = open("voiceover/core/separation.py", encoding="utf-8").read()
    assert "import torchaudio" not in src and "julius.resample_frac" in src
