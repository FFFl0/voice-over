"""Модель данных проекта озвучки и её сохранение в JSON."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

PROJECT_EXT = ".vop"
PROJECT_VERSION = 1

# Палитра для новых персонажей: тональный ночной набор (совпадает с ui.theme.CHARACTER_PALETTE)
CHARACTER_COLORS = [
    "#63bde7", "#bba2ea", "#ec9695", "#7fc48e", "#e2a16b", "#de97c6", "#52c6c0", "#c0b360",
    "#8ab2f4", "#ea9a7e", "#cf9bdb", "#a8bb6d", "#3a98c0", "#967dc2", "#c47171", "#5a9e6a",
    "#bb7c46", "#b772a0", "#1da09a", "#9b8d3a", "#668ccc", "#c2755a", "#a877b4", "#849548",
]


class MixMode(str, Enum):
    VOICEOVER = "voiceover"  # закадровый: оригинал приглушается под репликами
    DUBBING = "dubbing"      # дубляж: оригинальные голоса удалены, остаётся фон


class LineSource(str, Enum):
    TTS = "tts"              # синтез речи
    RECORDING = "recording"  # запись живого актёра


class Engine(str, Enum):
    EDGE = "edge"
    SILERO = "silero"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Character:
    name: str
    id: str = field(default_factory=new_id)
    color: str = "#63bde7"
    source: LineSource = LineSource.TTS
    engine: Engine = Engine.EDGE
    voice: str = "ru-RU-DmitryNeural"
    rate: int = 0        # скорость, % (-50..+100)
    pitch: int = 0       # высота, Гц (-50..+50)
    volume_db: float = 0.0
    actor: str = ""      # имя актёра озвучки (для живой записи)

    def tts_params(self) -> dict:
        return {
            "engine": self.engine.value,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["engine"] = self.engine.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        d = dict(d)
        d["source"] = LineSource(d.get("source", "tts"))
        d["engine"] = Engine(d.get("engine", "edge"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Line:
    start: float
    end: float
    text: str
    character_id: Optional[str] = None
    id: str = field(default_factory=new_id)
    enabled: bool = True
    # Синтезированный звук
    tts_path: str = ""
    tts_hash: str = ""
    # Запись живого актёра
    rec_path: str = ""
    rec_offset: float = 0.0  # смещение начала записи относительно start, сек
    gain_db: float = 0.0
    note: str = ""
    # Текст оригинала (до перевода) и его язык; пусто, если реплику не переводили
    orig_text: str = ""
    orig_lang: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Line":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class MixSettings:
    mode: MixMode = MixMode.VOICEOVER
    duck_db: float = -12.0        # приглушение оригинала под репликами (закадровый)
    duck_attack: float = 0.15     # время спада, сек
    duck_release: float = 0.35    # время восстановления, сек
    background_db: float = 0.0    # громкость фона/оригинала
    voices_db: float = 0.0        # общая громкость голосов
    normalize_voices: bool = True  # выравнивать громкость реплик
    max_speedup: float = 1.35     # максимум ускорения реплики, чтобы влезла в тайминг
    sample_rate: int = 48000

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MixSettings":
        d = dict(d)
        d["mode"] = MixMode(d.get("mode", "voiceover"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Project:
    video_path: str = ""
    characters: list[Character] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    mix: MixSettings = field(default_factory=MixSettings)
    audio_stream: int = 0         # индекс аудиодорожки оригинала
    duration: float = 0.0
    # Пути к служебным файлам (относительно папки проекта)
    original_wav: str = ""
    background_wav: str = ""      # фон без голосов (Demucs)
    path: str = ""                # где сохранён .vop (не сериализуется)

    # ---------- персонажи ----------
    def character(self, cid: Optional[str]) -> Optional[Character]:
        if cid is None:
            return None
        for c in self.characters:
            if c.id == cid:
                return c
        return None

    def character_by_name(self, name: str) -> Optional[Character]:
        low = name.strip().lower()
        for c in self.characters:
            if c.name.strip().lower() == low:
                return c
        return None

    def add_character(self, name: str, **kw) -> Character:
        if "color" not in kw:
            kw["color"] = CHARACTER_COLORS[len(self.characters) % len(CHARACTER_COLORS)]
        c = Character(name=name, **kw)
        self.characters.append(c)
        return c

    def ensure_character(self, name: str) -> Character:
        return self.character_by_name(name) or self.add_character(name)

    def remove_character(self, cid: str) -> None:
        self.characters = [c for c in self.characters if c.id != cid]
        for ln in self.lines:
            if ln.character_id == cid:
                ln.character_id = None

    def sort_lines(self) -> None:
        self.lines.sort(key=lambda ln: (ln.start, ln.end))

    # ---------- пути ----------
    @property
    def project_dir(self) -> Path:
        """Папка рядом с .vop для служебных файлов."""
        if self.path:
            p = Path(self.path)
            return p.with_name(p.stem + "_files")
        if self.video_path:
            p = Path(self.video_path)
            return p.with_name(p.stem + "_voiceover_files")
        return Path.cwd() / "voiceover_files"

    def resolve(self, rel: str) -> Optional[Path]:
        if not rel:
            return None
        p = Path(rel)
        if not p.is_absolute():
            p = self.project_dir / p
        return p

    def relative(self, p: Path) -> str:
        try:
            return str(Path(p).resolve().relative_to(self.project_dir.resolve()))
        except ValueError:
            return str(p)

    # ---------- сериализация ----------
    def to_dict(self) -> dict:
        return {
            "version": PROJECT_VERSION,
            "video_path": self.video_path,
            "audio_stream": self.audio_stream,
            "duration": self.duration,
            "original_wav": self.original_wav,
            "background_wav": self.background_wav,
            "mix": self.mix.to_dict(),
            "characters": [c.to_dict() for c in self.characters],
            "lines": [ln.to_dict() for ln in self.lines],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            video_path=d.get("video_path", ""),
            audio_stream=d.get("audio_stream", 0),
            duration=d.get("duration", 0.0),
            original_wav=d.get("original_wav", ""),
            background_wav=d.get("background_wav", ""),
            mix=MixSettings.from_dict(d.get("mix", {})),
            characters=[Character.from_dict(c) for c in d.get("characters", [])],
            lines=[Line.from_dict(x) for x in d.get("lines", [])],
        )

    # ---------- отмена/повтор ----------
    def snapshot(self) -> dict:
        """Редактируемое пользователем состояние (без путей к служебным файлам)."""
        return {
            "mix": self.mix.to_dict(),
            "characters": [c.to_dict() for c in self.characters],
            "lines": [ln.to_dict() for ln in self.lines],
        }

    def restore(self, state: dict) -> None:
        """Восстановить состояние на месте (объект проекта остаётся тем же)."""
        new_mix = MixSettings.from_dict(state["mix"])
        for k in MixSettings.__dataclass_fields__:
            setattr(self.mix, k, getattr(new_mix, k))
        self.characters = [Character.from_dict(c) for c in state["characters"]]
        self.lines = [Line.from_dict(x) for x in state["lines"]]

    def save(self, path: Optional[str] = None) -> None:
        if path:
            self.path = str(path)
        if not self.path:
            raise ValueError("Не указан путь проекта")
        tmp = Path(self.path).with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls, path: str) -> "Project":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        p = cls.from_dict(d)
        p.path = str(path)
        return p


def tts_hash(text: str, params: dict) -> str:
    """Ключ кэша синтеза: если текст или голос изменились — нужно пересинтезировать."""
    raw = json.dumps({"t": text.strip(), **params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def line_status(project: Project, line: Line) -> str:
    """Статус готовности реплики: 'off', 'rec', 'ok', 'stale', 'none', 'nochar'."""
    if not line.enabled or not line.text.strip() and not line.rec_path:
        return "off"
    ch = project.character(line.character_id)
    if ch is None:
        return "nochar"
    if ch.source == LineSource.RECORDING or line.rec_path:
        p = project.resolve(line.rec_path)
        return "rec" if p and p.exists() else "none"
    p = project.resolve(line.tts_path)
    if not p or not p.exists():
        return "none"
    if line.tts_hash != tts_hash(line.text, ch.tts_params()):
        return "stale"
    return "ok"
