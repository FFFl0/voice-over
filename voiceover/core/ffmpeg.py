"""Работа с FFmpeg: поиск, анализ медиа, извлечение звука, сборка итогового видео."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ProgressCb = Callable[[float], None]   # 0..1
CancelCb = Callable[[], bool]

_ffmpeg_override: Optional[str] = None


class FFmpegError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def set_ffmpeg_path(path: Optional[str]) -> None:
    global _ffmpeg_override
    _ffmpeg_override = path or None


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def find_ffmpeg() -> str:
    """Порядок поиска: настройка → папка ffmpeg рядом с программой → PATH → imageio-ffmpeg."""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if _ffmpeg_override and Path(_ffmpeg_override).is_file():
        return _ffmpeg_override
    for cand in (_app_dir() / "ffmpeg" / "bin" / exe, _app_dir() / "ffmpeg" / exe, _app_dir() / exe):
        if cand.is_file():
            return str(cand)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise FFmpegError(
        "FFmpeg не найден. Установите FFmpeg (https://ffmpeg.org), положите его в папку "
        "«ffmpeg» рядом с программой или укажите путь в настройках."
    )


def _popen_kwargs() -> dict:
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return kw


def run_ffmpeg(args: list[str], duration: float = 0.0,
               progress: Optional[ProgressCb] = None,
               cancel: Optional[CancelCb] = None) -> None:
    """Запуск ffmpeg с разбором прогресса (-progress pipe:1)."""
    cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1",
           "-nostats", "-loglevel", "error", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **_popen_kwargs())
    err_lines: list[str] = []

    import threading

    def _read_err():
        assert proc.stderr is not None
        for ln in proc.stderr:
            err_lines.append(ln)
            if len(err_lines) > 200:
                del err_lines[:100]

    t = threading.Thread(target=_read_err, daemon=True)
    t.start()
    assert proc.stdout is not None
    for ln in proc.stdout:
        if cancel and cancel():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if progress and duration > 0 and ln.startswith("out_time_us="):
            try:
                us = int(ln.split("=", 1)[1])
                progress(min(1.0, us / 1e6 / duration))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise FFmpegError("Ошибка FFmpeg:\n" + "".join(err_lines[-30:]))
    if progress:
        progress(1.0)


# ---------------------------------------------------------------- анализ

@dataclass
class StreamInfo:
    index: int          # глобальный индекс потока
    kind: str           # video/audio/subtitle
    type_index: int     # индекс среди потоков этого типа (для -map 0:a:N)
    codec: str
    language: str = ""
    title: str = ""
    description: str = ""


@dataclass
class MediaInfo:
    duration: float = 0.0
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def audio(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.kind == "audio"]

    @property
    def subtitles(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.kind == "subtitle"]

    @property
    def has_video(self) -> bool:
        return any(s.kind == "video" for s in self.streams)


_STREAM_RE = re.compile(r"Stream #\d+:(\d+)(?:\[[^\]]*\])?(?:\((\w+)\))?: (Video|Audio|Subtitle): (\w+)(.*)")


def probe(path: str) -> MediaInfo:
    """Анализ медиафайла через вывод `ffmpeg -i` (не требует ffprobe)."""
    proc = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", path],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **_popen_kwargs())
    out = proc.stderr
    if "No such file" in out or "Invalid data" in out:
        raise FFmpegError(f"Не удалось открыть файл:\n{out[-500:]}")
    info = MediaInfo()
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        info.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    counters = {"video": 0, "audio": 0, "subtitle": 0}
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        sm = _STREAM_RE.search(ln)
        if not sm:
            continue
        kind = sm.group(3).lower()
        st = StreamInfo(index=int(sm.group(1)), kind=kind, type_index=counters[kind],
                        codec=sm.group(4), language=sm.group(2) or "",
                        description=sm.group(5).strip(" ,"))
        counters[kind] += 1
        # Ищем title в метаданных потока
        for nxt in lines[i + 1:i + 8]:
            if "Stream #" in nxt:
                break
            tm = re.match(r"\s+title\s*:\s*(.*)", nxt)
            if tm:
                st.title = tm.group(1).strip()
                break
        info.streams.append(st)
    return info


# ---------------------------------------------------------------- операции

def extract_audio(video: str, out_wav: str, stream: int = 0, sample_rate: int = 48000,
                  duration: float = 0.0, progress: Optional[ProgressCb] = None,
                  cancel: Optional[CancelCb] = None) -> None:
    """Извлечь аудиодорожку в WAV (PCM 16 бит, стерео)."""
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", video, "-map", f"0:a:{stream}", "-vn", "-ac", "2",
                "-ar", str(sample_rate), "-c:a", "pcm_s16le", out_wav],
               duration, progress, cancel)


def _atempo_chain(factor: float) -> str:
    parts = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.5f}")
    return ",".join(parts)


def convert_audio(src: str, dst: str, sample_rate: int = 48000, tempo: float = 1.0,
                  mono: bool = True) -> None:
    """Перекодировать в WAV с нужной частотой, при необходимости изменить темп без смены тона."""
    af = []
    if abs(tempo - 1.0) > 1e-3:
        af.append(_atempo_chain(tempo))
    args = ["-i", src]
    if af:
        args += ["-af", ",".join(af)]
    args += ["-ac", "1" if mono else "2", "-ar", str(sample_rate), "-c:a", "pcm_f32le", dst]
    run_ffmpeg(args)


@dataclass
class ExportOptions:
    container: str = "mkv"          # mkv | mp4
    codec: str = "aac"              # aac | ac3 | flac | opus
    bitrate: str = "256k"
    keep_original: bool = True
    keep_subtitles: bool = True
    track_title: str = "Многоголосая озвучка"
    language: str = "rus"


AUDIO_CODECS = {
    "aac": ("aac", "AAC"),
    "ac3": ("ac3", "AC3 (Dolby Digital)"),
    "opus": ("libopus", "Opus"),
    "flac": ("flac", "FLAC (без потерь)"),
}


def mux_video(video: str, mix_wav: str, out_path: str, opts: ExportOptions,
              original_stream: int = 0, duration: float = 0.0,
              progress: Optional[ProgressCb] = None,
              cancel: Optional[CancelCb] = None) -> None:
    """Собрать видео: видео без перекодирования + новая дорожка (по умолчанию) + оригинал."""
    enc, _ = AUDIO_CODECS.get(opts.codec, ("aac", ""))
    mp4 = opts.container == "mp4"
    args = ["-i", video, "-i", mix_wav, "-map", "0:v?", "-map", "1:a:0"]
    if opts.keep_original:
        args += ["-map", f"0:a:{original_stream}"]
    if opts.keep_subtitles:
        args += ["-map", "0:s?"]
    args += ["-c:v", "copy", "-c:a:0", enc]
    if opts.codec != "flac":
        args += ["-b:a:0", opts.bitrate]
    if opts.keep_original:
        # MP4 поддерживает не все кодеки — оригинал перекодируем в AAC
        args += ["-c:a:1", "aac" if mp4 else "copy"]
        if mp4:
            args += ["-b:a:1", "192k"]
    if opts.keep_subtitles:
        args += ["-c:s", "mov_text" if mp4 else "copy"]
    args += ["-metadata:s:a:0", f"title={opts.track_title}",
             "-metadata:s:a:0", f"language={opts.language}",
             "-disposition:a:0", "default"]
    if opts.keep_original:
        args += ["-metadata:s:a:1", "title=Оригинал", "-disposition:a:1", "0"]
    if mp4:
        args += ["-movflags", "+faststart"]
    args += ["-max_interleave_delta", "0", out_path]
    run_ffmpeg(args, duration, progress, cancel)


def encode_audio(mix_wav: str, out_path: str, opts: ExportOptions,
                 duration: float = 0.0, progress: Optional[ProgressCb] = None,
                 cancel: Optional[CancelCb] = None) -> None:
    """Экспорт только звуковой дорожки."""
    ext = Path(out_path).suffix.lower()
    if ext == ".wav":
        args = ["-i", mix_wav, "-c:a", "pcm_s16le", out_path]
    else:
        enc, _ = AUDIO_CODECS.get(opts.codec, ("aac", ""))
        args = ["-i", mix_wav, "-c:a", enc]
        if opts.codec != "flac":
            args += ["-b:a", opts.bitrate]
        args += ["-metadata:s:a:0", f"title={opts.track_title}",
                 "-metadata:s:a:0", f"language={opts.language}", out_path]
    run_ffmpeg(args, duration, progress, cancel)
