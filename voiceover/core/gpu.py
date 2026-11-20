"""Проверка, можно ли считать на видеокарте NVIDIA через CTranslate2 (Whisper, перевод NLLB).

Наличие видеокарты ещё не значит, что CUDA работает: CTranslate2 нужны библиотеки
cuBLAS 12 и cuDNN 9 (cublas64_12.dll, cudnn*64_9.dll). Если их нет, ошибка
«Library cublas64_12.dll is not found or cannot be loaded» возникает только во время
работы, поэтому библиотеки проверяются заранее, а при сбое работа повторяется на процессоре.
"""

from __future__ import annotations

import ctypes
import os
import sys
from functools import lru_cache
from pathlib import Path

WIN_LIBS = ("cublas64_12.dll", "cublasLt64_12.dll")
LINUX_LIBS = ("libcublas.so.12", "libcublasLt.so.12")


def _add_pip_cuda_dirs() -> None:
    """Пакеты nvidia-*-cu12 кладут DLL в site-packages/nvidia/*/bin — добавляем их в поиск."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if nv.is_dir():
            for sub in nv.glob("*/bin"):
                try:
                    os.add_dll_directory(str(sub))
                    os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


@lru_cache(maxsize=1)
def cuda_usable() -> bool:
    """Есть видеокарта CUDA и загружаются нужные ей библиотеки."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return False
    except Exception:  # noqa: BLE001
        return False
    _add_pip_cuda_dirs()
    loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
    for lib in (WIN_LIBS if sys.platform == "win32" else LINUX_LIBS):
        try:
            loader(lib)
        except OSError:
            return False
    return True


def is_cuda_error(e: BaseException) -> bool:
    """Ошибка из-за отсутствующих или несовместимых библиотек CUDA."""
    msg = str(e).lower()
    return any(k in msg for k in ("cublas", "cudnn", "cuda", "cannot be loaded", "cudart"))
