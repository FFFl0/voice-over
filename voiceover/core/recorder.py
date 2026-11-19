"""Запись голоса с микрофона (sounddevice)."""

from __future__ import annotations

import queue
import threading
from typing import Optional

import numpy as np
import soundfile as sf


def sd_available() -> tuple[bool, str]:
    try:
        import sounddevice  # noqa: F401
    except Exception as e:  # noqa: BLE001  (нет PortAudio и т.п.)
        return False, f"Запись недоступна: {e}"
    return True, ""


def input_devices() -> list[tuple[int, str]]:
    ok, _ = sd_available()
    if not ok:
        return []
    import sounddevice as sd
    out = []
    try:
        hostapis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                api = hostapis[d["hostapi"]]["name"]
                out.append((i, f"{d['name']} [{api}]"))
    except Exception:
        pass
    return out


def find_device(name: str) -> Optional[int]:
    for i, n in input_devices():
        if n == name:
            return i
    return None


class Recorder:
    """Пишет звук с микрофона в WAV, параллельно сообщает уровень сигнала."""

    def __init__(self, device: Optional[int] = None, sample_rate: int = 48000, channels: int = 1):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream = None
        self._writer: Optional[threading.Thread] = None
        self._file: Optional[sf.SoundFile] = None
        self._running = False
        self.level = 0.0         # пиковый уровень 0..1 последнего блока
        self.frames = 0

    def _callback(self, indata, frames, time, status):  # noqa: ARG002
        self.level = float(np.abs(indata).max()) if frames else 0.0
        if self._running:
            self._q.put(indata.copy())

    def start_monitor(self) -> None:
        """Открыть поток только для индикатора уровня (без записи)."""
        import sounddevice as sd
        if self._stream is None:
            self._stream = sd.InputStream(device=self.device, channels=self.channels,
                                          samplerate=self.sample_rate, dtype="float32",
                                          callback=self._callback, blocksize=1024)
            self._stream.start()

    def start(self, path: str) -> None:
        self.start_monitor()
        self._file = sf.SoundFile(path, "w", self.sample_rate, self.channels, "PCM_24")
        self.frames = 0
        while not self._q.empty():
            self._q.get_nowait()
        self._running = True
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    def _write_loop(self):
        while self._running or not self._q.empty():
            try:
                block = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            assert self._file is not None
            self._file.write(block)
            self.frames += len(block)

    def stop(self) -> float:
        """Остановить запись, вернуть длительность в секундах."""
        self._running = False
        if self._writer:
            self._writer.join(timeout=3)
            self._writer = None
        if self._file:
            self._file.close()
            self._file = None
        return self.frames / self.sample_rate

    @property
    def recording(self) -> bool:
        return self._running

    def close(self) -> None:
        if self._running:
            self.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
