"""Silero TTS — офлайн-синтез русской речи (нужен PyTorch, модель скачивается один раз)."""

from __future__ import annotations

import threading
from pathlib import Path

from .base import TTSEngine, TTSError, Voice

MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SAMPLE_RATE = 48000

VOICES = [
    Voice("aidar", "Айдар", "M"),
    Voice("eugene", "Евгений", "M"),
    Voice("baya", "Бая", "F"),
    Voice("kseniya", "Ксения", "F"),
    Voice("xenia", "Ксения 2", "F"),
]


def models_dir() -> Path:
    from ..core.settings import data_dir
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SileroEngine(TTSEngine):
    id = "silero"
    title = "Silero (офлайн)"
    offline = True

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, "Для Silero нужен PyTorch (pip install torch)"
        return True, ""

    def voices(self) -> list[Voice]:
        return list(VOICES)

    def _load(self):
        if self._model is not None:
            return self._model
        import torch
        path = models_dir() / "silero_v4_ru.pt"
        if not path.exists():
            tmp = path.with_suffix(".part")
            torch.hub.download_url_to_file(MODEL_URL, str(tmp), progress=False)
            tmp.replace(path)
        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
        model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        model.to(torch.device("cpu"))
        self._model = model
        return model

    def synthesize(self, text: str, voice: str, out_path: str,
                   rate: int = 0, pitch: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise TTSError(why)
        import numpy as np
        import soundfile as sf
        with self._lock:
            model = self._load()
            # SSML позволяет задать темп и высоту
            ssml_rate = "medium" if rate == 0 else f"{100 + rate}%"
            ssml_pitch = "medium" if pitch == 0 else f"{'+' if pitch > 0 else ''}{pitch}%"
            safe = (text.replace("&", " и ").replace("<", " ").replace(">", " "))
            ssml = f'<speak><prosody rate="{ssml_rate}" pitch="{ssml_pitch}">{safe}</prosody></speak>'
            try:
                audio = model.apply_tts(ssml_text=ssml, speaker=voice, sample_rate=SAMPLE_RATE)
            except Exception:
                audio = model.apply_tts(text=safe, speaker=voice, sample_rate=SAMPLE_RATE)
        out = str(Path(out_path).with_suffix(".wav"))
        sf.write(out, np.asarray(audio, dtype="float32"), SAMPLE_RATE)
        return out
