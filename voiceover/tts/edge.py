"""Microsoft Edge TTS — бесплатные нейроголоса (нужен интернет)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import TTSEngine, TTSError, Voice

# Голоса, которые умеют говорить по-русски. Многоязычные (Multilingual) голоса
# читают русский текст с лёгким акцентом — это добавляет разнообразия персонажам.
RU_VOICES = [
    Voice("ru-RU-DmitryNeural", "Дмитрий", "M"),
    Voice("ru-RU-SvetlanaNeural", "Светлана", "F"),
    Voice("ru-RU-DariyaNeural", "Дарья", "F"),
    Voice("en-US-AndrewMultilingualNeural", "Andrew (мульти)", "M"),
    Voice("en-US-BrianMultilingualNeural", "Brian (мульти)", "M"),
    Voice("en-US-AvaMultilingualNeural", "Ava (мульти)", "F"),
    Voice("en-US-EmmaMultilingualNeural", "Emma (мульти)", "F"),
    Voice("de-DE-FlorianMultilingualNeural", "Florian (мульти)", "M"),
    Voice("de-DE-SeraphinaMultilingualNeural", "Seraphina (мульти)", "F"),
    Voice("fr-FR-RemyMultilingualNeural", "Remy (мульти)", "M"),
    Voice("fr-FR-VivienneMultilingualNeural", "Vivienne (мульти)", "F"),
    Voice("it-IT-GiuseppeMultilingualNeural", "Giuseppe (мульти)", "M"),
    Voice("ko-KR-HyunsuMultilingualNeural", "Hyunsu (мульти)", "M"),
]


NO_AUDIO_HELP = (
    "Edge TTS не вернул звук. Это не ошибка в настройках персонажа — сервис Microsoft не ответил.\n\n"
    "Что можно сделать:\n"
    "1. Обновить библиотеку: запустите setup.bat (или pip install -U edge-tts) — Microsoft "
    "периодически меняет протокол, и старые версии перестают работать.\n"
    "2. Если не помогло — сервис может быть недоступен из вашей сети: попробуйте VPN.\n"
    "3. Переключите персонажей на офлайн-движок: «Персонажи» → «Раздать голоса» → Vosk TTS "
    "(много русских голосов) или Piper "
    "(или «Движок» в настройках персонажа). Интернет нужен только для первой загрузки голоса.")


def _signed(v: int, unit: str) -> str:
    return f"{'+' if v >= 0 else ''}{int(v)}{unit}"


class EdgeEngine(TTSEngine):
    id = "edge"
    title = "Edge TTS (онлайн, бесплатно)"
    offline = False
    concurrency = 4

    def available(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False, "Не установлен пакет edge-tts (pip install edge-tts)"
        return True, ""

    def voices(self) -> list[Voice]:
        return list(RU_VOICES)

    async def _one(self, text: str, voice: str, out_path: str, rate: int, pitch: int) -> str:
        import edge_tts
        out = str(Path(out_path).with_suffix(".mp3"))
        last: Exception | None = None
        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(text, voice, rate=_signed(rate, "%"),
                                            pitch=_signed(pitch, "Hz"))
                await comm.save(out)
                if Path(out).stat().st_size == 0:
                    raise TTSError("Пустой ответ сервера")
                return out
            except Exception as e:  # noqa: BLE001
                last = e
                await asyncio.sleep(1.5 * (attempt + 1))
        if type(last).__name__ == "NoAudioReceived":
            raise TTSError(NO_AUDIO_HELP)
        raise TTSError(f"Edge TTS: {last}")

    def synthesize(self, text: str, voice: str, out_path: str,
                   rate: int = 0, pitch: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise TTSError(why)
        return asyncio.run(self._one(text, voice, out_path, rate, pitch))

    def synthesize_many(self, jobs, on_done=None, cancel=None):
        ok, why = self.available()
        if not ok:
            raise TTSError(why)
        errors: list = [None] * len(jobs)

        async def runner():
            sem = asyncio.Semaphore(self.concurrency)

            async def one(i, job):
                async with sem:
                    if cancel and cancel():
                        return
                    text, voice, out, rate, pitch = job
                    try:
                        path = await self._one(text, voice, out, rate, pitch)
                        if on_done:
                            on_done(i, path, None)
                    except Exception as e:  # noqa: BLE001
                        errors[i] = e
                        if on_done:
                            on_done(i, None, e)

            await asyncio.gather(*(one(i, j) for i, j in enumerate(jobs)))

        asyncio.run(runner())
        return errors
