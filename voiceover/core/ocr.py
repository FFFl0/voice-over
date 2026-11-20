"""Субтитры из текста на видео (жёсткие/вшитые субтитры) с помощью OCR.

Как это работает:
1. FFmpeg отдаёт кадры только из области субтитров (по умолчанию нижняя четверть),
   несколько кадров в секунду.
2. Для каждого кадра строится «маска текста»: по умолчанию светлые пиксели, рядом с
   которыми есть тёмная обводка, — так выглядят почти все субтитры.
3. Пока маска не меняется, это одна реплика; при заметном изменении начинается новая.
   Так OCR запускается один раз на реплику, а не на каждый кадр.
4. Средний кадр каждой реплики распознаётся OCR; соседние одинаковые реплики склеиваются.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

import numpy as np

from . import ffmpeg
from .ffmpeg import Cancelled
from .subtitles import SubLine

# (код, название, движки в порядке предпочтения)
LANGUAGES = [
    ("ru", "Русский", ("easyocr",)),
    ("en", "Английский", ("rapidocr", "easyocr")),
    ("ja", "Японский", ("easyocr",)),
    ("ko", "Корейский", ("easyocr",)),
    ("zh", "Китайский", ("rapidocr", "easyocr")),
    ("uk", "Украинский", ("easyocr",)),
    ("de", "Немецкий", ("easyocr",)),
    ("fr", "Французский", ("easyocr",)),
    ("es", "Испанский", ("easyocr",)),
]
EASYOCR_CODES = {"zh": "ch_sim", "ja": "ja", "ko": "ko", "ru": "ru", "en": "en",
                 "uk": "uk", "de": "de", "fr": "fr", "es": "es"}

MODES = [("light", "Светлый текст с обводкой (обычные субтитры)"),
         ("dark", "Тёмный текст на светлом"),
         ("any", "Любой текст (по контурам, медленнее и шумнее)")]


@dataclass
class OcrRegion:
    """Область поиска текста в долях кадра."""
    left: float = 0.05
    top: float = 0.70
    width: float = 0.90
    height: float = 0.28

    def pixels(self, w: int, h: int) -> tuple[int, int, int, int]:
        x = int(w * max(0.0, min(1.0, self.left)))
        y = int(h * max(0.0, min(1.0, self.top)))
        cw = int(w * self.width)
        ch = int(h * self.height)
        cw = max(16, min(cw, w - x)) // 2 * 2
        ch = max(16, min(ch, h - y)) // 2 * 2
        return x, y, cw, ch


@dataclass
class OcrSettings:
    language: str = "en"
    region: OcrRegion = field(default_factory=OcrRegion)
    fps: float = 4.0
    mode: str = "light"
    min_duration: float = 0.4     # короче — скорее всего мелькание, не реплика
    change_threshold: float = 0.5   # относительное изменение маски, после которого начинается новая реплика
    max_width: int = 1280         # кадры уменьшаются до этой ширины


# ---------------------------------------------------------------- движки OCR

def engines_available() -> dict[str, tuple[bool, str]]:
    out = {}
    try:
        import rapidocr_onnxruntime  # noqa: F401
        out["rapidocr"] = (True, "")
    except ImportError:
        out["rapidocr"] = (False, "pip install rapidocr_onnxruntime")
    try:
        import easyocr  # noqa: F401
        out["easyocr"] = (True, "")
    except ImportError:
        out["easyocr"] = (False, "pip install easyocr (нужен PyTorch — AI-модули в setup.bat)")
    return out


def pick_engine(language: str) -> tuple[Optional[str], str]:
    """Подходящий установленный движок для языка или (None, подсказка по установке)."""
    prefs = next((p for c, _, p in LANGUAGES if c == language), ("easyocr",))
    avail = engines_available()
    for e in prefs:
        if avail.get(e, (False, ""))[0]:
            return e, ""
    hints = "; ".join(f"{e}: {avail[e][1]}" for e in prefs if e in avail)
    return None, f"Нет движка OCR для выбранного языка. Установите: {hints}"


class OcrEngine:
    def read(self, rgb: np.ndarray) -> list[tuple[float, float, str, float]]:
        """-> [(y_center, x_left, text, confidence)]"""
        raise NotImplementedError


class RapidEngine(OcrEngine):
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()

    def read(self, rgb):
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        result, _ = self.ocr(bgr)
        out = []
        for box, text, conf in result or []:
            pts = np.asarray(box, dtype=float)
            out.append((float(pts[:, 1].mean()), float(pts[:, 0].min()), str(text), float(conf)))
        return out


class EasyEngine(OcrEngine):
    def __init__(self, language: str, gpu: Optional[bool] = None):
        import easyocr
        code = EASYOCR_CODES.get(language, language)
        langs = [code] if code == "en" else [code, "en"]
        if gpu is None:
            try:
                import torch
                gpu = torch.cuda.is_available()
            except Exception:  # noqa: BLE001
                gpu = False
        from .settings import data_dir
        model_dir = data_dir() / "models" / "easyocr"
        model_dir.mkdir(parents=True, exist_ok=True)
        self.reader = easyocr.Reader(langs, gpu=gpu, model_storage_directory=str(model_dir),
                                     verbose=False)

    def read(self, rgb):
        out = []
        for box, text, conf in self.reader.readtext(rgb):
            pts = np.asarray(box, dtype=float)
            out.append((float(pts[:, 1].mean()), float(pts[:, 0].min()), str(text), float(conf)))
        return out


def make_engine(name: str, language: str) -> OcrEngine:
    if name == "rapidocr":
        return RapidEngine()
    if name == "easyocr":
        return EasyEngine(language)
    raise ValueError(name)


def boxes_to_text(items: list[tuple[float, float, str, float]], min_conf: float = 0.5,
                  line_tol: float = 12.0) -> str:
    """Собрать найденные фрагменты в одну реплику: строки сверху вниз, слева направо."""
    good = [i for i in items if i[3] >= min_conf and i[2].strip()]
    if not good:
        return ""
    good.sort(key=lambda i: (i[0], i[1]))
    lines: list[list[tuple[float, float, str, float]]] = []
    for it in good:
        if lines and abs(it[0] - lines[-1][0][0]) <= line_tol:
            lines[-1].append(it)
        else:
            lines.append([it])
    parts = [" ".join(t[2].strip() for t in sorted(ln, key=lambda i: i[1])) for ln in lines]
    return " ".join(parts).strip()


# ---------------------------------------------------------------- кадры и маска

def frames(video: str, settings: OcrSettings, size: tuple[int, int], t_from: float = 0.0,
           t_to: Optional[float] = None,
           cancel: Optional[Callable[[], bool]] = None) -> Iterator[tuple[float, np.ndarray]]:
    """Кадры области субтитров: (время, RGB-массив)."""
    w, h = size
    x, y, cw, ch = settings.region.pixels(w, h)
    scale = min(1.0, settings.max_width / cw)
    ow, oh = max(16, int(cw * scale) // 2 * 2), max(16, int(ch * scale) // 2 * 2)
    vf = f"fps={settings.fps},crop={cw}:{ch}:{x}:{y},scale={ow}:{oh}"
    cmd = [ffmpeg.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if t_from > 0:
        cmd += ["-ss", f"{t_from:.3f}"]
    cmd += ["-i", video]
    if t_to is not None:
        cmd += ["-t", f"{max(0.1, t_to - t_from):.3f}"]
    cmd += ["-an", "-sn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            **ffmpeg._popen_kwargs())
    frame_bytes = ow * oh * 3
    n = 0
    try:
        assert proc.stdout is not None
        while True:
            if cancel and cancel():
                raise Cancelled()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield t_from + n / settings.fps, np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
            n += 1
    finally:
        proc.kill()
        proc.wait()


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out |= np.roll(np.roll(m, dy, axis=0), dx, axis=1)
    return out


def text_mask(rgb: np.ndarray, mode: str = "light", rim_radius: int = 4) -> np.ndarray:
    """Пиксели букв субтитров. «light»: почти белые (все три канала яркие, цвет не насыщен),
    рядом с тёмной обводкой; «dark» — наоборот; «any» — контуры любого текста."""
    rgb16 = rgb.astype(np.int16)
    lo = rgb16.min(axis=2)
    hi = rgb16.max(axis=2)
    if mode == "any":
        gray = rgb16.mean(axis=2)
        gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1]))
        gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
        return (gx + gy) > 90
    if mode == "dark":
        core = hi < 70
        rim = (lo > 175) & (hi - lo < 60)
    else:
        core = (lo > 175) & (hi - lo < 60)
        rim = hi < 70
    return core & _dilate(rim, rim_radius)


def _drop_tall(m: np.ndarray, max_run: int) -> np.ndarray:
    """Убрать вертикальные штрихи выше строки текста (края кадра, линии декораций)."""
    h = m.shape[0]
    run = np.zeros(m.shape, np.int32)
    run[0] = m[0]
    for y in range(1, h):
        run[y] = (run[y - 1] + 1) * m[y]
    total = run.copy()
    for y in range(h - 2, -1, -1):
        cont = m[y] & m[y + 1]
        total[y] = np.where(cont, total[y + 1], total[y])
    return m & (total <= max_run)


def clean_for_ocr(rgb: np.ndarray, mode: str = "light") -> np.ndarray:
    """Чёрные буквы на белом фоне без картинки под ними: OCR читает такое гораздо надёжнее."""
    if mode == "any":
        return rgb
    m = _drop_tall(text_mask(rgb, mode), int(rgb.shape[0] * 0.45))
    m = _dilate(m, 1)   # заполнить разрывы внутри букв
    img = np.where(m, 0, 255).astype(np.uint8)
    img = np.pad(img, 20, constant_values=255)
    return np.stack([img] * 3, axis=-1)


def signature(mask: np.ndarray, grid: int = 12) -> np.ndarray:
    """Грубая карта заполненности маски — устойчива к шуму и сжатию видео."""
    h, w = mask.shape
    gh, gw = max(1, h // grid), max(1, w // grid)
    m = mask[: gh * (h // gh), : gw * (w // gw)]
    return m.reshape(h // gh, gh, w // gw, gw).mean(axis=(1, 3))


@dataclass
class _Seg:
    start: float
    end: float
    sig: np.ndarray
    frames: list[np.ndarray]
    pixels: int


def detect_segments(stream: Iterator[tuple[float, np.ndarray]], settings: OcrSettings,
                    progress: Optional[Callable[[float], None]] = None) -> list[_Seg]:
    """Разбить поток кадров на отрезки с неизменным текстом."""
    segs: list[_Seg] = []
    cur: Optional[_Seg] = None
    step = 1.0 / settings.fps
    for t, rgb in stream:
        mask = text_mask(rgb, settings.mode)
        px = int(mask.sum())
        has_text = px > mask.size * 0.0015
        sig = signature(mask)
        if cur is not None:
            scale = max(cur.sig.mean(), sig.mean(), 1e-4)
            diff = float(np.abs(sig - cur.sig).mean() / scale)
            if not has_text or diff > settings.change_threshold:
                segs.append(cur)
                cur = None
            else:
                cur.end = t + step
                if len(cur.frames) < 60:
                    cur.frames.append(rgb)
                cur.sig = cur.sig * 0.7 + sig * 0.3
        if cur is None and has_text:
            cur = _Seg(t, t + step, sig, [rgb], px)
        if progress:
            progress(t)
    if cur is not None:
        segs.append(cur)
    return [s for s in segs if s.end - s.start >= settings.min_duration]


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def merge_lines(lines: list[SubLine], max_gap: float = 0.35, min_sim: float = 0.82) -> list[SubLine]:
    out: list[SubLine] = []
    for s in lines:
        if out and s.start - out[-1].end <= max_gap and similar(s.text, out[-1].text) >= min_sim:
            out[-1].end = s.end
            if len(s.text) > len(out[-1].text):
                out[-1].text = s.text
        else:
            out.append(s)
    return out


def extract_subtitles(video: str, settings: OcrSettings, engine: Optional[OcrEngine] = None,
                      t_from: float = 0.0, t_to: Optional[float] = None,
                      progress: Optional[Callable[[float, str], None]] = None,
                      cancel: Optional[Callable[[], bool]] = None) -> list[SubLine]:
    """Найти вшитые субтитры и вернуть реплики с таймингами."""
    info = ffmpeg.probe(video)
    size = info.video_size
    if not all(size):
        raise RuntimeError("В файле нет видеопотока или не удалось определить размер кадра.")
    end = min(info.duration, t_to) if t_to is not None else info.duration
    total = max(0.1, end - t_from)
    if engine is None:
        name, hint = pick_engine(settings.language)
        if name is None:
            raise RuntimeError(hint)
        if progress:
            progress(0.0, "Загрузка модели OCR (при первом запуске скачивается)…")
        engine = make_engine(name, settings.language)

    def p_scan(t: float):
        if progress:
            progress(0.6 * (t - t_from) / total, f"Поиск субтитров в кадре: {t / 60:.1f} / {end / 60:.1f} мин")

    segs = detect_segments(frames(video, settings, size, t_from, t_to, cancel), settings, p_scan)
    out: list[SubLine] = []
    for k, sg in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        frame = sg.frames[len(sg.frames) // 2]
        text = boxes_to_text(engine.read(clean_for_ocr(frame, settings.mode)))
        if len(text) < 2:   # не вышло на очищенном кадре — пробуем исходный
            text = boxes_to_text(engine.read(frame))
        if len(text) >= 2:
            out.append(SubLine(round(sg.start, 3), round(sg.end, 3), text))
        if progress:
            progress(0.6 + 0.4 * (k + 1) / max(1, len(segs)),
                     f"Распознавание текста: {k + 1} из {len(segs)}")
    return merge_lines(out)


def grab_frame(video: str, t: float) -> Optional[np.ndarray]:
    """Один кадр целиком (для предпросмотра области в диалоге)."""
    info = ffmpeg.probe(video)
    w, h = info.video_size
    if not w:
        return None
    scale = min(1.0, 960 / w)
    ow, oh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    cmd = [ffmpeg.find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{max(0.0, t):.3f}", "-i", video, "-frames:v", "1",
           "-vf", f"scale={ow}:{oh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, **ffmpeg._popen_kwargs())
    if len(r.stdout) < ow * oh * 3:
        return None
    return np.frombuffer(r.stdout[: ow * oh * 3], np.uint8).reshape(oh, ow, 3)
