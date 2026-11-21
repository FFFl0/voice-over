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
        self.monitor = None      # LiveFx — «слышать себя» в наушниках (None — выкл.)
        self.monitor_volume = 1.0

    def _callback(self, indata, frames, time, status):  # noqa: ARG002
        self.level = float(np.abs(indata).max()) if frames else 0.0
        if self._running:
            self._q.put(indata.copy())

    def _duplex_callback(self, indata, outdata, frames, time, status):  # noqa: ARG002
        self._callback(indata, frames, time, status)
        fx = self.monitor
        if fx is None:
            outdata.fill(0)
            return
        y = fx.process(indata[:, 0]) * self.monitor_volume
        outdata[:] = y[:, None]

    def _output_device(self):
        """Выход того же звукового API, что и микрофон (иначе Windows не откроет поток)."""
        import sounddevice as sd
        try:
            dev = self.device if self.device is not None else sd.default.device[0]
            api = sd.query_devices(dev)["hostapi"]
            out = sd.query_hostapis(api)["default_output_device"]
            return out if out is not None and out >= 0 else None
        except Exception:  # noqa: BLE001
            return None

    def start_monitor(self) -> None:
        """Открыть поток: индикатор уровня, а при включённом «слышать себя» — и выход."""
        import sounddevice as sd
        if self._stream is None:
            if self.monitor is not None:
                self._stream = sd.Stream(device=(self.device, self._output_device()),
                                         channels=(self.channels, 2), samplerate=self.sample_rate,
                                         dtype="float32", callback=self._duplex_callback,
                                         blocksize=256, latency="low")
            else:
                self._stream = sd.InputStream(device=self.device, channels=self.channels,
                                              samplerate=self.sample_rate, dtype="float32",
                                              callback=self._callback, blocksize=1024)
            self._stream.start()

    def set_monitor(self, fx) -> None:
        """Включить (LiveFx) или выключить (None) «слышать себя». Поток переоткрывается,
        поэтому во время записи переключать нельзя."""
        if self._running:
            raise RuntimeError("Нельзя переключать во время записи")
        if (fx is None) == (self.monitor is None):
            self.monitor = fx      # тот же режим — просто сменить эффект
            return
        self._close_stream()
        self.monitor = fx
        try:
            self.start_monitor()
        except Exception:
            self.monitor = None
            self._close_stream()
            self.start_monitor()
            raise

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
        self._close_stream()

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
