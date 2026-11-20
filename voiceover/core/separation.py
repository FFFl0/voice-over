"""Отделение голосов от музыки и эффектов с помощью Demucs (нужен PyTorch).

Работает в отдельном процессе: если PyTorch аварийно завершится, программа
останется открытой.
Фильм обрабатывается кусками с перекрытием, чтобы не держать весь звук в памяти,
а куски склеиваются плавным перекрёстным переходом.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .ffmpeg import Cancelled

SEGMENT_SEC = 120.0
OVERLAP_SEC = 2.0
CPU_SEGMENT_SEC = 60.0
WORKER_FLAG = "--separate-worker"


def demucs_available() -> tuple[bool, str]:
    """Проверка без импорта: PyTorch загружается только в процессе отделения голосов,
    а не в окне программы (сотни мегабайт памяти и риск конфликта библиотек)."""
    from importlib.util import find_spec
    if find_spec("torch") is None or find_spec("demucs") is None:
        return False, ("Для удаления оригинальных голосов нужны PyTorch и Demucs:\n"
                       "pip install torch demucs\n(см. README, раздел «Установка»)")
    return True, ""


def pick_device(pref: str = "auto") -> str:
    import torch
    if pref == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def process_in_chunks(in_wav: str, out_background: str, out_vocals: Optional[str],
                      vocals_fn: Callable[[np.ndarray, int], np.ndarray],
                      progress: Optional[Callable[[float, str], None]] = None,
                      cancel: Optional[Callable[[], bool]] = None,
                      segment_sec: float = SEGMENT_SEC, overlap_sec: float = OVERLAP_SEC) -> None:
    """Прогнать файл кусками через vocals_fn(x[n,2], sr) -> голоса[n,2].
    Фон = исходник − голоса. Соседние куски перекрываются и склеиваются кроссфейдом."""
    src = sf.SoundFile(in_wav)
    sr = src.samplerate
    total = src.frames
    seg = int(segment_sec * sr)
    ov = int(overlap_sec * sr)
    Path(out_background).parent.mkdir(parents=True, exist_ok=True)
    bg_part = out_background + ".part"
    vo_part = (out_vocals + ".part") if out_vocals else None
    bg_out = sf.SoundFile(bg_part, "w", sr, 2, "PCM_16", format="WAV")
    vo_out = sf.SoundFile(vo_part, "w", sr, 2, "PCM_16", format="WAV") if vo_part else None
    prev_bg_tail: Optional[np.ndarray] = None
    prev_vo_tail: Optional[np.ndarray] = None
    pos = 0
    try:
        while pos < total:
            if cancel and cancel():
                raise Cancelled()
            # первые `head` сэмплов — контекст (уже записаны), затем зона кроссфейда
            start = max(0, pos - ov)
            head = pos - start
            src.seek(start)
            x = src.read(min(total - start, seg + head), dtype="float32", always_2d=True)
            if x.shape[1] == 1:
                x = np.repeat(x, 2, axis=1)
            x = x[:, :2]
            vocals = np.asarray(vocals_fn(x, sr), dtype=np.float32)[: len(x)]
            if len(vocals) < len(x):
                vocals = np.pad(vocals, ((0, len(x) - len(vocals)), (0, 0)))
            background = x - vocals  # всё, кроме голосов
            if prev_bg_tail is not None and len(prev_bg_tail):
                k = min(len(prev_bg_tail), len(x) - head)
                fade = np.linspace(0, 1, k, dtype=np.float32)[:, None]
                background[head:head + k] = prev_bg_tail[:k] * (1 - fade) + background[head:head + k] * fade
                vocals[head:head + k] = prev_vo_tail[:k] * (1 - fade) + vocals[head:head + k] * fade
            end_main = len(x) if start + len(x) >= total else len(x) - ov
            bg_out.write(np.clip(background[head:end_main], -1, 1))
            if vo_out:
                vo_out.write(np.clip(vocals[head:end_main], -1, 1))
            prev_bg_tail = background[end_main:]
            prev_vo_tail = vocals[end_main:]
            pos = start + end_main
            if progress:
                progress(pos / total, f"Отделение голосов: {pos / sr / 60:.1f} / {total / sr / 60:.1f} мин")
    except BaseException:
        bg_out.close()
        if vo_out:
            vo_out.close()
        src.close()
        Path(bg_part).unlink(missing_ok=True)
        if vo_part:
            Path(vo_part).unlink(missing_ok=True)
        raise
    bg_out.close()
    if vo_out:
        vo_out.close()
    src.close()
    Path(bg_part).replace(out_background)
    if out_vocals and vo_part:
        Path(vo_part).replace(out_vocals)


def _send(msg: dict) -> None:
    import json
    out = sys.stdout
    if out is not None:
        out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        out.flush()


def separate_in_process(in_wav: str, out_background: str, out_vocals: Optional[str] = None,
                        model_name: str = "htdemucs", device: str = "auto",
                        progress: Optional[Callable[[float, str], None]] = None,
                        cancel: Optional[Callable[[], bool]] = None) -> str:
    """Само отделение голосов (в текущем процессе). Возвращает устройство, на котором считали."""
    ok, why = demucs_available()
    if not ok:
        raise RuntimeError(why)
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    # Оставляем пару ядер интерфейсу и системе, иначе компьютер «замирает»
    torch.set_num_threads(max(1, min(8, (os.cpu_count() or 2) - 1)))
    if progress:
        progress(0.0, "Загрузка модели Demucs (при первом запуске скачивается)…")
    model = get_model(model_name)
    model.eval()
    dev = pick_device(device)
    model.to(dev)
    msr = model.samplerate
    vocals_idx = model.sources.index("vocals")
    # На процессоре куски короче: меньше памяти на промежуточные данные модели
    segment = SEGMENT_SEC if dev == "cuda" else CPU_SEGMENT_SEC

    def resample(x: np.ndarray, a: int, b: int) -> np.ndarray:
        if a == b:
            return x
        # julius (зависимость demucs, чистый PyTorch) вместо torchaudio: бинарная библиотека
        # torchaudio часто не загружается, если её версия не совпадает с версией PyTorch
        import julius
        t = torch.from_numpy(np.ascontiguousarray(x.T))
        return julius.resample_frac(t, a, b).numpy().T

    def vocals_fn(x: np.ndarray, sr: int) -> np.ndarray:
        wav = torch.from_numpy(np.ascontiguousarray(resample(x, sr, msr).T)).to(dev)
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        with torch.inference_mode():
            out = apply_model(model, ((wav - mean) / std)[None], device=dev,
                              split=True, overlap=0.25, progress=False)[0]
            voc = (out[vocals_idx] * std + mean).cpu().numpy()
        del out, wav
        if dev == "cuda":
            torch.cuda.empty_cache()
        return resample(voc.T, msr, sr)

    process_in_chunks(in_wav, out_background, out_vocals, vocals_fn, progress, cancel,
                      segment_sec=segment)
    return dev


def worker_main(args: list[str]) -> int:
    """Точка входа отдельного процесса: python … --separate-worker '<json>'.
    Прогресс и результат — строками JSON в stdout."""
    import json
    import traceback
    a = json.loads(args[0])
    try:
        dev = separate_in_process(a["in"], a["bg"], a.get("vocals"), a.get("model", "htdemucs"),
                                  a.get("device", "auto"),
                                  lambda f, t: _send({"p": f, "t": t}))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _send({"error": str(e) or e.__class__.__name__,
               "cuda": _looks_like_gpu_error(e)})
        return 2
    _send({"done": 1, "device": dev})
    return 0


def _looks_like_gpu_error(e: BaseException) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("cuda", "cublas", "cudnn", "out of memory", "gpu"))


def worker_command(payload: str) -> list[str]:
    if getattr(sys, "frozen", False):   # собранный .exe: тот же файл с ключом
        return [sys.executable, WORKER_FLAG, payload]
    code = ("import sys; from voiceover.core.separation import worker_main; "
            "sys.exit(worker_main(sys.argv[1:]))")
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):   # у pythonw нет stdout — берём python.exe рядом
        alt = Path(exe).with_name("python.exe")
        exe = str(alt) if alt.exists() else exe
    return [exe, "-c", code, payload]


def _run_worker(args: dict, progress, cancel, log_file: Path) -> dict:
    """Запустить отделение в дочернем процессе. Падение PyTorch (нехватка памяти, сбой
    драйвера, конфликт библиотек OpenMP) завершит только его, а не всю программу."""
    import json
    import subprocess
    env = dict(os.environ)
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"     # libiomp5 из PyTorch и из других модулей
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("TQDM_DISABLE", "1")
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    kw = {}
    if os.name == "nt":   # без окна консоли и с пониженным приоритетом — интерфейс не тормозит
        kw["creationflags"] = 0x08000000 | 0x00004000   # CREATE_NO_WINDOW | BELOW_NORMAL
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(worker_command(json.dumps(args, ensure_ascii=False)),
                                stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                cwd=root, env=env, **kw)
        import queue
        import threading
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def reader():
            for raw in proc.stdout:   # type: ignore[union-attr]
                q.put(raw.decode("utf-8", "replace"))
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        result: dict = {}
        try:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                try:
                    line = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue   # посторонний вывод библиотек
                if "p" in msg and progress:
                    progress(float(msg["p"]), str(msg.get("t", "")))
                elif "error" in msg or "done" in msg:
                    result = msg
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    result.setdefault("code", proc.returncode)
    return result


def _tail(path: Path, n: int = 12) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def separate_vocals(in_wav: str, out_background: str, out_vocals: Optional[str] = None,
                    model_name: str = "htdemucs", device: str = "auto",
                    progress: Optional[Callable[[float, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None) -> None:
    """Разделить звук на голоса и фон (музыка + эффекты) моделью Demucs — в отдельном процессе.
    Если на видеокарте не вышло (ошибка или аварийное завершение), повторяет на процессоре."""
    ok, why = demucs_available()
    if not ok:
        raise RuntimeError(why)
    log_file = Path(out_background).with_name("separation.log")
    args = {"in": in_wav, "bg": out_background, "vocals": out_vocals,
            "model": model_name, "device": device}
    res = _run_worker(args, progress, cancel, log_file)
    if res.get("done"):
        return
    if device != "cpu" and (res.get("cuda") or "error" not in res):
        # видеокарта: нехватка видеопамяти, нет библиотек CUDA или сбой драйвера
        if progress:
            progress(0.0, "Видеокарта не справилась — отделение голосов на процессоре…")
        res = _run_worker(dict(args, device="cpu"), progress, cancel, log_file)
        if res.get("done"):
            return
    if "error" in res:
        raise RuntimeError(f"Отделение голосов: {res['error']}")
    code = res.get("code")
    hint = ""
    if code in (-9, 137, 3221225477, -1073741819, 3221225725, -1073741571):
        hint = " Похоже, не хватило памяти: закройте другие программы и попробуйте ещё раз."
    raise RuntimeError(
        f"Процесс отделения голосов аварийно завершился (код {code}).{hint}\n"
        f"Подробности: {log_file}\n\n{_tail(log_file)}".rstrip())
