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
    bundled = Path(getattr(sys, "_MEIPASS", _app_dir()))    # LGPL-сборка внутри .exe-папки
    for cand in (_app_dir() / "ffmpeg" / "bin" / exe, _app_dir() / "ffmpeg" / exe, _app_dir() / exe,
                 bundled / "ffmpeg" / "bin" / exe):
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
               cancel: Optional[CancelCb] = None, cwd: Optional[str] = None) -> None:
    """Запуск ffmpeg с разбором прогресса (-progress pipe:1)."""
    cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1",
           "-nostats", "-loglevel", "error", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
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
    width: int = 0       # для видеопотока
    height: int = 0
    fps: float = 0.0


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

    @property
    def video_size(self) -> tuple[int, int]:
        for s in self.streams:
            if s.kind == "video" and s.width and s.height:
                return s.width, s.height
        return 0, 0


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
        if kind == "video":
            dm = re.search(r"\b(\d{2,5})x(\d{2,5})\b", st.description)
            if dm:
                st.width, st.height = int(dm.group(1)), int(dm.group(2))
            fm = re.search(r"(\d+(?:\.\d+)?) fps", st.description)
            if fm:
                st.fps = float(fm.group(1))
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


def track_label(st: StreamInfo) -> str:
    """Короткая подпись звуковой дорожки: язык, название, кодек и каналы."""
    parts = []
    if st.language and st.language != "und":
        parts.append(st.language)
    if st.title:
        parts.append(f"«{st.title}»")
    ch = re.search(r"\b(mono|stereo|[1-7]\.[01](?:\(\w+\))?)", st.description)
    parts.append(st.codec.upper() + (f" {ch.group(1)}" if ch else ""))
    return " · ".join(parts)


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
                  mono: bool = True, filters: str = "") -> None:
    """Перекодировать в WAV с нужной частотой, при необходимости изменить темп без смены тона."""
    af = []
    if abs(tempo - 1.0) > 1e-3:
        af.append(_atempo_chain(tempo))
    if filters:   # эффекты рассчитаны на 48 кГц — сначала приводим частоту
        af += [f"aresample={sample_rate}", filters]
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
    all_originals: bool = True          # все дорожки исходного файла, а не только основную
    keep_subtitles: bool = True
    track_title: str = "Многоголосая озвучка"
    language: str = "rus"
    loudness: Optional[float] = None   # целевая громкость, LUFS (None — не менять)
    # Оформление (видео перекодируется): логотип, вшитые субтитры, заставка в начале
    logo: str = ""
    logo_pos: str = "tr"                # tl | tr | bl | br
    logo_size: float = 0.12             # ширина логотипа — доля ширины кадра
    logo_opacity: float = 0.85
    burn_subs: str = ""                 # ASS-файл для вшивания
    intro: str = ""                     # видео-заставка перед фильмом
    crf: int = 20                       # качество перекодирования H.264

    @property
    def reencode(self) -> bool:
        return bool(self.logo or self.burn_subs or self.intro)


AUDIO_CODECS = {
    "aac": ("aac", "AAC"),
    "ac3": ("ac3", "AC3 (Dolby Digital)"),
    "opus": ("libopus", "Opus"),
    "flac": ("flac", "FLAC (без потерь)"),
}


def _original_tracks(video: str, opts: "ExportOptions", main: int) -> list[tuple[int, str]]:
    """Какие дорожки исходника оставить: [(номер среди аудио, название для метаданных)]."""
    if not opts.keep_original:
        return []
    try:
        audio = probe(video).audio
    except Exception:  # noqa: BLE001
        audio = []
    if not audio:
        return [(main, "Оригинал")]
    keep = audio if opts.all_originals else [a for a in audio if a.type_index == main] or audio[:1]
    out = []
    for a in keep:
        title = a.title or ("Оригинал" + (f" ({a.language})" if a.language and
                                          a.language != "und" else ""))
        out.append((a.type_index, title))
    return out


def _original_args(tracks: list[tuple[int, str]], mp4: bool, sources: list[str]) -> list[str]:
    """Кодеки и метаданные дорожек оригинала (выходные индексы 1…N)."""
    args = []
    for j, ((_k, title), src) in enumerate(zip(tracks, sources), start=1):
        args += [f"-c:a:{j}", "aac" if mp4 else "copy"]
        if mp4:
            args += [f"-b:a:{j}", "192k"]
        args += [f"-metadata:s:a:{j}", f"title={title}", f"-disposition:a:{j}", "0"]
    return args


def has_filter(name: str) -> bool:
    try:
        out = subprocess.run([find_ffmpeg(), "-hide_banner", "-filters"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             **_popen_kwargs()).stdout
    except Exception:  # noqa: BLE001
        return False
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", out, re.M) is not None


_encoders_cache: dict[str, str] = {}


def has_encoder(name: str) -> bool:
    exe = find_ffmpeg()
    if exe not in _encoders_cache:
        try:
            _encoders_cache[exe] = subprocess.run(
                [exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", **_popen_kwargs()).stdout
        except Exception:  # noqa: BLE001
            _encoders_cache[exe] = ""
    return re.search(rf"^\s*V\S*\s+{re.escape(name)}\s", _encoders_cache[exe], re.M) is not None


def h264_args(crf: int, width: int, height: int, fps: float) -> list[str]:
    """Параметры видеокодировщика. libx264 (GPL) есть только в FFmpeg, который пользователь
    поставил сам; в поставляемой LGPL-сборке — OpenH264 или кодировщик Windows (Media
    Foundation). Им качество задаётся битрейтом: он подбирается по размеру кадра и crf."""
    if has_encoder("libx264"):
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf)]
    bpp = 0.1 * 2 ** ((20 - crf) / 6)                 # бит на пиксель; crf 20 ≈ 5 Мбит/с для 1080p25
    kbps = int(max(800, min(40000, width * height * max(fps, 1.0) * bpp / 1000)))
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k", "-bufsize", f"{kbps * 2}k"]
    for enc in ("libopenh264", "h264_mf"):
        if has_encoder(enc):
            return ["-c:v", enc] + rate
    return ["-c:v", "mpeg4", "-q:v", str(max(2, min(10, crf // 5)))]


def filter_name(name: str) -> str:
    """Имя файла для параметра фильтра: FFmpeg запускается из папки файла, поэтому путь
    с буквой диска и спецсимволами в граф фильтров не попадает; само имя экранируем."""
    s = name
    for ch in ("\\", ":", "'", "[", "]", ",", ";", "=", " "):
        s = s.replace(ch, "\\\\" + ch if ch in "\\'" else "\\" + ch)
    return s


_LOGO_XY = {"tl": ("{m}", "{m}"), "tr": ("main_w-overlay_w-{m}", "{m}"),
            "bl": ("{m}", "main_h-overlay_h-{m}"), "br": ("main_w-overlay_w-{m}", "main_h-overlay_h-{m}")}


def _mux_reencode(video: str, mix_wav: str, out_path: str, opts: ExportOptions,
                  original_stream: int, duration: float, progress, cancel) -> None:
    """Видео с оформлением: логотип, вшитые субтитры, заставка — с перекодированием H.264."""
    info = probe(video)
    vw, vh = info.video_size
    if not vw:
        raise FFmpegError("В исходном файле нет видео")
    fps = next((s.fps for s in info.streams if s.kind == "video" and s.fps), 25.0)
    enc, _ = AUDIO_CODECS.get(opts.codec, ("aac", ""))
    mp4 = opts.container == "mp4"
    args = ["-i", video, "-i", mix_wav]
    nxt = 2
    graph, v = [], "[0:v]"
    if opts.logo:
        args += ["-i", opts.logo]
        li = nxt
        nxt += 1
        m = max(8, vw // 50)
        x, y = (c.format(m=m) for c in _LOGO_XY.get(opts.logo_pos, _LOGO_XY["tr"]))
        lw = max(16, int(vw * opts.logo_size)) // 2 * 2
        graph.append(f"[{li}:v]format=rgba,scale={lw}:-2,"
                     f"colorchannelmixer=aa={max(0.05, min(1.0, opts.logo_opacity)):.2f}[lg]")
        graph.append(f"{v}[lg]overlay={x}:{y}[vl]")
        v = "[vl]"
    if opts.burn_subs:
        graph.append(f"{v}subtitles=filename={filter_name(Path(opts.burn_subs).name)}[vs]")
        v = "[vs]"
    a0 = "1:a:0"
    tracks = _original_tracks(video, opts, original_stream)
    orig_maps = [f"0:a:{k}" for k, _t in tracks]
    intro_dur = 0.0
    if opts.intro:
        ii = probe(opts.intro)
        intro_dur = ii.duration
        args += ["-i", opts.intro]
        k = nxt
        nxt += 1
        graph.append(f"[{k}:v]scale={vw}:{vh}:force_original_aspect_ratio=decrease,"
                     f"pad={vw}:{vh}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p[iv]")
        graph.append(f"{v}setsar=1,fps={fps:g},format=yuv420p[mv]")
        graph.append("[iv][mv]concat=n=2:v=1:a=0[vc]")
        v = "[vc]"
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if ii.audio:
            graph.append(f"[{k}:a:0]{fmt},apad=whole_dur={intro_dur:.3f}[ia]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{intro_dur:.3f}[ia]")
        graph.append(f"[1:a:0]{fmt}[ma]")
        graph.append("[ia][ma]concat=n=2:v=0:a=1[a0]")
        a0 = "[a0]"
        ms = int(round(intro_dur * 1000))
        for j, (k, _t) in enumerate(tracks):
            graph.append(f"[0:a:{k}]adelay=delays={ms}:all=1[o{j}]")
        orig_maps = [f"[o{j}]" for j in range(len(tracks))]
    if v == "[0:v]":
        graph.append("[0:v]null[vo]")
        v = "[vo]"
    args += ["-filter_complex", ";".join(graph), "-map", v, "-map", a0]
    for m in orig_maps:
        args += ["-map", m]
    keep_subs = opts.keep_subtitles and not opts.intro   # после заставки они бы съехали
    if keep_subs:
        args += ["-map", "0:s?"]
    args += h264_args(opts.crf, vw, vh, fps) + ["-pix_fmt", "yuv420p", "-c:a:0", enc]
    if opts.codec != "flac":
        args += ["-b:a:0", opts.bitrate]
    # после заставки дорожки сдвинуты фильтром — их нужно перекодировать
    args += _original_args(tracks, mp4 or bool(opts.intro), orig_maps)
    if keep_subs:
        args += ["-c:s", "mov_text" if mp4 else "copy"]
    args += ["-metadata:s:a:0", f"title={opts.track_title}",
             "-metadata:s:a:0", f"language={opts.language}", "-disposition:a:0", "default"]
    if mp4:
        args += ["-movflags", "+faststart"]
    args += [out_path]
    # пути входов абсолютные; рабочая папка — папка субтитров (см. filter_name)
    abs_args = [str(Path(a).resolve()) if i > 0 and args[i - 1] == "-i" else a
                for i, a in enumerate(args)]
    abs_args[-1] = str(Path(out_path).resolve())
    cwd = str(Path(opts.burn_subs).resolve().parent) if opts.burn_subs else None
    run_ffmpeg(abs_args, duration + intro_dur, progress, cancel, cwd=cwd)


def mux_video(video: str, mix_wav: str, out_path: str, opts: ExportOptions,
              original_stream: int = 0, duration: float = 0.0,
              progress: Optional[ProgressCb] = None,
              cancel: Optional[CancelCb] = None) -> None:
    """Собрать видео: видео без перекодирования + новая дорожка (по умолчанию) + оригинал.
    С оформлением (логотип, субтитры, заставка) видео перекодируется."""
    if opts.reencode:
        _mux_reencode(video, mix_wav, out_path, opts, original_stream, duration, progress, cancel)
        return
    enc, _ = AUDIO_CODECS.get(opts.codec, ("aac", ""))
    mp4 = opts.container == "mp4"
    tracks = _original_tracks(video, opts, original_stream)
    args = ["-i", video, "-i", mix_wav, "-map", "0:v?", "-map", "1:a:0"]
    for k, _t in tracks:
        args += ["-map", f"0:a:{k}"]
    if opts.keep_subtitles:
        args += ["-map", "0:s?"]
    args += ["-c:v", "copy", "-c:a:0", enc]
    if opts.codec != "flac":
        args += ["-b:a:0", opts.bitrate]
    args += _original_args(tracks, mp4, [f"0:a:{k}" for k, _t in tracks])
    if opts.keep_subtitles:
        args += ["-c:s", "mov_text" if mp4 else "copy"]
    args += ["-metadata:s:a:0", f"title={opts.track_title}",
             "-metadata:s:a:0", f"language={opts.language}",
             "-disposition:a:0", "default"]
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


# ---------------------------------------------------------------- громкость (EBU R128)

LOUDNESS_PRESETS = [
    (None, "Не менять"),
    (-23.0, "−23 LUFS — EBU R128, телевидение (стандарт)"),
    (-16.0, "−16 LUFS — онлайн-видео"),
    (-14.0, "−14 LUFS — стриминговые сервисы, громче"),
]


def measure_loudness(wav: str, target: float = -23.0, true_peak: float = -1.0,
                     lra: float = 11.0, cancel: Optional[CancelCb] = None) -> dict:
    """Первый проход loudnorm: измерить интегральную громкость, пик и диапазон."""
    import json as _json
    cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-i", wav, "-af",
           f"loudnorm=I={target}:TP={true_peak}:LRA={lra}:print_format=json",
           "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **_popen_kwargs())
    assert proc.stderr is not None
    out = []
    for ln in proc.stderr:
        out.append(ln)
        if cancel and cancel():
            proc.kill()
            proc.wait()
            raise Cancelled()
    proc.wait()
    text = "".join(out)
    start, end = text.rfind("{"), text.rfind("}")
    if proc.returncode != 0 or start < 0 or end < start:
        raise FFmpegError("Не удалось измерить громкость:\n" + text[-800:])
    return _json.loads(text[start:end + 1])


def normalize_loudness(src: str, dst: str, target: float, true_peak: float = -1.0,
                       lra: float = 11.0, sample_rate: int = 48000, duration: float = 0.0,
                       progress: Optional[ProgressCb] = None,
                       cancel: Optional[CancelCb] = None) -> dict:
    """Двухпроходная нормализация громкости к target LUFS. Возвращает измерения до обработки."""
    m = measure_loudness(src, target, true_peak, lra, cancel)
    if progress:
        progress(0.5)
    try:
        measured_i = float(m["input_i"])
    except (KeyError, ValueError):
        measured_i = float("-inf")
    if measured_i == float("-inf") or measured_i < -70:
        # тишина — нормализовать нечего, копируем как есть
        run_ffmpeg(["-i", src, "-c:a", "pcm_s24le", dst], duration, None, cancel)
        return m
    af = (f"loudnorm=I={target}:TP={true_peak}:LRA={lra}"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true")
    run_ffmpeg(["-i", src, "-af", af, "-ar", str(sample_rate), "-c:a", "pcm_s24le", dst],
               duration, (lambda f: progress(0.5 + f * 0.5)) if progress else None, cancel)
    return m
