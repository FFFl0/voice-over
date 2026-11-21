"""Глобальные настройки приложения (JSON в профиле пользователя)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    d = Path(base) / "VoiceOverStudio"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class AppSettings:
    ffmpeg_path: str = ""
    input_device: str = ""          # имя устройства записи
    record_preroll: float = 2.0     # сек видео перед репликой при записи
    record_postroll: float = 1.0
    record_countdown: bool = True
    record_auto_align: bool = True  # подгонять запись к оригиналу сразу после записи
    demucs_model: str = "htdemucs"
    demucs_device: str = "auto"     # auto | cuda | cpu
    theme: str = "dark"             # dark | light
    rythmo_band: bool = True        # ритмо-лента под видео
    spell_lang: str = "ru"          # проверка орфографии: ru | en | uk | "" (выкл.)
    whisper_model: str = "large-v3-turbo"
    whisper_language: str = ""      # "" — автоопределение
    whisper_device: str = "auto"
    whisper_max_len: float = 7.0
    export_loudness: Optional[float] = -23.0   # LUFS; None — не нормализовать
    translate_source: str = "auto"
    translate_target: str = "ru"
    translate_engine: str = ""      # local | google | deepl ("" — выбрать автоматически)
    nllb_size: str = "600M"
    deepl_key: str = ""
    recent: list[str] = field(default_factory=list)

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        try:
            d = json.loads(cls.path().read_text(encoding="utf-8"))
            known = cls.__dataclass_fields__
            return cls(**{k: v for k, v in d.items() if k in known})
        except Exception:
            return cls()

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1),
                               encoding="utf-8")

    def add_recent(self, path: str) -> None:
        path = str(path)
        self.recent = [p for p in self.recent if p != path]
        self.recent.insert(0, path)
        del self.recent[10:]
