"""Piper — лёгкий офлайн-синтез речи (onnxruntime, без PyTorch и без интернета после загрузки голоса).

Голоса скачиваются один раз (~60 МБ каждый) из репозитория rhasspy/piper-voices.
Темп задаётся параметром модели, высота — сдвигом тона в FFmpeg с сохранением длительности.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

from .base import TTSEngine, TTSError, Voice

URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
       "{family}/{code}/{name}/{quality}/{code}-{name}-{quality}{ext}?download=true")

VOICES = [
    Voice("ru_RU-dmitri-medium", "Дмитрий", "M"),
    Voice("ru_RU-denis-medium", "Денис", "M"),
    Voice("ru_RU-ruslan-medium", "Руслан", "M"),
    Voice("ru_RU-irina-medium", "Ирина", "F"),
    Voice("uk_UA-lada-x_low", "Лада (укр.)", "F", "uk"),
    Voice("en_US-ryan-high", "Ryan (англ.)", "M", "en"),
    Voice("en_US-amy-medium", "Amy (англ.)", "F", "en"),
]


def voices_dir() -> Path:
    from ..core.settings import data_dir
    d = data_dir() / "models" / "piper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def voice_files(voice_id: str) -> tuple[Path, Path]:
    d = voices_dir()
    return d / f"{voice_id}.onnx", d / f"{voice_id}.onnx.json"


def download_voice(voice_id: str) -> Path:
    """Скачать голос (модель и её настройки), если его ещё нет."""
    model, cfg = voice_files(voice_id)
    if model.exists() and cfg.exists():
        return model
    try:
        code, name, quality = voice_id.split("-", 2)
    except ValueError:
        raise TTSError(f"Неверный ID голоса Piper: {voice_id}")
    family = code.split("_")[0]
    import requests
    for path, ext in ((cfg, ".onnx.json"), (model, ".onnx")):
        if path.exists():
            continue
        url = URL.format(family=family, code=code, name=name, quality=quality, ext=ext)
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                if r.status_code == 404:
                    raise TTSError(f"Голос Piper «{voice_id}» не найден")
                r.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                tmp.replace(path)
        except TTSError:
            raise
        except Exception as e:  # noqa: BLE001
            raise TTSError(f"Не удалось скачать голос Piper «{voice_id}»: {e}")
    return model


def custom_voices() -> list[Voice]:
    """Голоса, добавленные пользователем: любые .onnx + .onnx.json в папке голосов Piper.
    У многоголосых моделей каждый голос — отдельный пункт («модель#номер»)."""
    import json
    known = {v.id for v in VOICES}
    out: list[Voice] = []
    for model in sorted(voices_dir().glob("*.onnx")):
        vid = model.name[:-5]
        cfg_path = model.with_name(model.name + ".json")
        if vid in known or not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        lang = str((cfg.get("language") or {}).get("code", "")).split("_")[0] or "ru"
        n = int(cfg.get("num_speakers") or 1)
        if n <= 1:
            out.append(Voice(vid, f"{vid} (свой)", "", lang))
        else:
            names = {i: s for s, i in (cfg.get("speaker_id_map") or {}).items()}
            for k in range(n):
                out.append(Voice(f"{vid}#{k}", f"{vid}: {names.get(k, k + 1)} (свой)", "", lang))
    return out


def pitch_factor(pitch: int) -> float:
    """Ползунок «Высота» (−50…+50, как у Edge в Гц) → множитель тона ±33%."""
    return max(0.6, min(1.5, 1.0 + pitch / 150.0))


def shift_pitch(wav: str, factor: float) -> None:
    """Сдвинуть тон без изменения длительности (FFmpeg: asetrate + atempo)."""
    if abs(factor - 1.0) < 0.01:
        return
    from ..core import ffmpeg
    import soundfile as sf
    sr = sf.info(wav).samplerate
    tmp = str(Path(wav).with_suffix(".pitch.wav"))
    ffmpeg.run_ffmpeg(["-i", wav, "-af",
                       f"asetrate={int(sr * factor)},aresample={sr},{ffmpeg._atempo_chain(1 / factor)}",
                       tmp])
    Path(tmp).replace(wav)


class PiperEngine(TTSEngine):
    id = "piper"
    title = "Piper (офлайн, лёгкий)"
    offline = True

    def __init__(self):
        self._loaded: dict[str, object] = {}
        self._lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False, "Для Piper нужен пакет piper-tts (pip install piper-tts)"
        return True, ""

    def voices(self) -> list[Voice]:
        return list(VOICES) + custom_voices()

    def _voice(self, voice_id: str):
        model_id = voice_id.split("#", 1)[0]
        v = self._loaded.get(model_id)
        if v is None:
            from piper import PiperVoice
            model, cfg = voice_files(model_id)
            if not (model.exists() and cfg.exists()):
                model = download_voice(model_id)
            v = PiperVoice.load(str(model))
            self._loaded[model_id] = v
        return v

    def synthesize(self, text: str, voice: str, out_path: str,
                   rate: int = 0, pitch: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise TTSError(why)
        out = str(Path(out_path).with_suffix(".wav"))
        with self._lock:
            v = self._voice(voice)
            from piper import SynthesisConfig
            # темп: +50% → длительность ×0.67; −50% → ×2
            length_scale = 1.0 / max(0.5, 1.0 + rate / 100.0)
            speaker = int(voice.split("#", 1)[1]) if "#" in voice else None
            cfg = SynthesisConfig(length_scale=length_scale)
            if speaker is not None:
                cfg.speaker_id = speaker
            with wave.open(out, "wb") as wf:
                v.synthesize_wav(text, wf, syn_config=cfg)
        shift_pitch(out, pitch_factor(pitch))
        return out
