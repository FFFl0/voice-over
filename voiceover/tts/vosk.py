"""Vosk TTS (Alphacephei) — офлайн-синтез русской речи с множеством голосов в одной модели.

Работает на onnxruntime (как Piper и Whisper), PyTorch не нужен. Многоголосая русская модель
скачивается один раз с alphacephei.com; в новых версиях модели десятки голосов, в старых — пять.
Число голосов берётся из конфигурации скачанной модели.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

from .base import TTSEngine, TTSError, Voice

MODEL_PRE_URL = "https://alphacephei.com/vosk/models/"
MODEL_LIST_URL = MODEL_PRE_URL + "model-list.json"
# Если список моделей недоступен — пробуем известные имена, от новых к старым
KNOWN_MODELS = ["vosk-model-tts-ru-0.10-multi", "vosk-model-tts-ru-0.9-multi",
                "vosk-model-tts-ru-0.8-multi", "vosk-model-tts-ru-0.7-multi"]
# Голоса модели 0.7-multi (по карточке модели): три женских, два мужских
DEFAULT_VOICES = [("0", "Голос 1", "F"), ("1", "Голос 2", "F"), ("2", "Голос 3", "F"),
                  ("3", "Голос 4", "M"), ("4", "Голос 5", "M")]


def models_dir() -> Path:
    from ..core.settings import data_dir
    d = data_dir() / "models" / "vosk-tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version(name: str) -> tuple:
    m = re.search(r"-(\d+)\.(\d+)", name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def installed_model() -> Optional[Path]:
    """Скачанная модель с самой новой версией (или None)."""
    cands = [p for p in models_dir().glob("vosk-model-tts-ru*")
             if p.is_dir() and (p / "model.onnx").exists() and (p / "config.json").exists()]
    if not cands:
        return None
    return max(cands, key=lambda p: (_version(p.name), "multi" in p.name))


def pick_model_name(model_list: Optional[list] = None) -> list[str]:
    """Имена русских многоголосых TTS-моделей по убыванию версии."""
    names = []
    for m in model_list or []:
        n = str(m.get("name", ""))
        if n.startswith("vosk-model-tts-ru") and str(m.get("obsolete", "false")) != "true":
            names.append(n)
    obsolete = {str(m.get("name")) for m in model_list or []
                if str(m.get("obsolete", "false")) == "true"}
    names += [n for n in KNOWN_MODELS if n not in names and n not in obsolete]
    names.sort(key=lambda n: (_version(n), "multi" in n), reverse=True)
    return names


def download_model(progress=None) -> Path:
    """Скачать и распаковать модель (один раз)."""
    found = installed_model()
    if found:
        return found
    import requests
    import zipfile
    try:
        listing = requests.get(MODEL_LIST_URL, timeout=15).json()
    except Exception:  # noqa: BLE001
        listing = None
    errors = []
    for name in pick_model_name(listing):
        zpath = models_dir() / f"{name}.zip"
        try:
            if progress:
                progress(f"Скачивание модели Vosk TTS ({name}), это делается один раз…")
            with requests.get(MODEL_PRE_URL + name + ".zip", stream=True, timeout=60) as r:
                if r.status_code == 404:
                    errors.append(f"{name}: нет на сервере")
                    continue
                r.raise_for_status()
                tmp = zpath.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(zpath)
            if progress:
                progress("Распаковка модели Vosk TTS…")
            with zipfile.ZipFile(zpath) as z:
                z.extractall(models_dir())
            zpath.unlink(missing_ok=True)
            found = installed_model()
            if found:
                return found
            errors.append(f"{name}: в архиве нет model.onnx")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            zpath.unlink(missing_ok=True)
    raise TTSError("Не удалось скачать модель Vosk TTS:\n" + "\n".join(errors))


def speaker_count(config: dict) -> int:
    for getter in (lambda c: c.get("num_speakers"),
                   lambda c: c.get("n_speakers"),
                   lambda c: (c.get("data") or {}).get("n_speakers"),
                   lambda c: len(c.get("speaker_id_map") or {}) or None):
        try:
            n = getter(config)
        except Exception:  # noqa: BLE001
            n = None
        if isinstance(n, int) and n > 0:
            return n
    return len(DEFAULT_VOICES)


class VoskEngine(TTSEngine):
    id = "vosk"
    title = "Vosk TTS (офлайн, много русских голосов)"
    offline = True

    def __init__(self):
        self._synth = None
        self._lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        try:
            import vosk_tts  # noqa: F401
        except ImportError:
            return False, "Для Vosk TTS нужен пакет vosk-tts (pip install vosk-tts)"
        return True, ""

    def voices(self) -> list[Voice]:
        model = installed_model()
        if model is None:
            return [Voice(i, n, g) for i, n, g in DEFAULT_VOICES]
        try:
            cfg = json.loads((model / "config.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
        n = speaker_count(cfg)
        known = {i: g for i, _, g in DEFAULT_VOICES} if n == len(DEFAULT_VOICES) else {}
        return [Voice(str(k), f"Голос {k + 1}", known.get(str(k), "")) for k in range(n)]

    def _get_synth(self, progress=None):
        if self._synth is None:
            from vosk_tts import Model, Synth
            path = download_model(progress)
            self._synth = Synth(Model(model_path=str(path)))
        return self._synth

    def synthesize(self, text: str, voice: str, out_path: str,
                   rate: int = 0, pitch: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise TTSError(why)
        try:
            sid = int(voice)
        except ValueError:
            raise TTSError(f"Неверный номер голоса Vosk TTS: {voice}")
        out = str(Path(out_path).with_suffix(".wav"))
        with self._lock:
            synth = self._get_synth()
            speech_rate = max(0.5, 1.0 + rate / 100.0)
            synth.synth(text, out, speaker_id=sid, speech_rate=speech_rate)
        from .piper import pitch_factor, shift_pitch
        shift_pitch(out, pitch_factor(pitch))
        return out
