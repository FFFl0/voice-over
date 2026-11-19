"""Общий интерфейс движков синтеза речи."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class TTSError(RuntimeError):
    pass


@dataclass
class Voice:
    id: str
    name: str
    gender: str = ""   # "M" / "F"
    language: str = "ru"

    @property
    def label(self) -> str:
        g = {"M": "муж.", "F": "жен."}.get(self.gender, "")
        return f"{self.name} ({g})" if g else self.name


class TTSEngine:
    id = ""
    title = ""
    offline = False

    def available(self) -> tuple[bool, str]:
        """(доступен ли движок, причина если нет)."""
        return True, ""

    def voices(self) -> list[Voice]:
        raise NotImplementedError

    def synthesize(self, text: str, voice: str, out_path: str,
                   rate: int = 0, pitch: int = 0) -> str:
        """Синтезировать текст в файл. Возвращает фактический путь (расширение может отличаться)."""
        raise NotImplementedError

    def synthesize_many(self, jobs: list[tuple[str, str, str, int, int]],
                        on_done=None, cancel=None) -> list[Optional[Exception]]:
        """jobs: (text, voice, out_path, rate, pitch). По умолчанию — последовательно."""
        errors: list[Optional[Exception]] = []
        for i, (text, voice, out, rate, pitch) in enumerate(jobs):
            if cancel and cancel():
                break
            try:
                path = self.synthesize(text, voice, out, rate, pitch)
                errors.append(None)
                if on_done:
                    on_done(i, path, None)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                if on_done:
                    on_done(i, None, e)
        return errors


_ENGINES: dict[str, TTSEngine] = {}


def _registry() -> dict[str, TTSEngine]:
    if not _ENGINES:
        from .edge import EdgeEngine
        from .silero import SileroEngine
        for e in (EdgeEngine(), SileroEngine()):
            _ENGINES[e.id] = e
    return _ENGINES


def get_engine(engine_id: str) -> TTSEngine:
    reg = _registry()
    if engine_id not in reg:
        raise TTSError(f"Неизвестный движок: {engine_id}")
    return reg[engine_id]


def list_engines() -> list[TTSEngine]:
    return list(_registry().values())
