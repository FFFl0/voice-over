# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: pyinstaller voiceover.spec   (VO_FULL=1 — включить PyTorch/Demucs)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

full = os.environ.get("VO_FULL") == "1"
datas = collect_data_files("imageio_ffmpeg", include_py_files=False)
datas += [("voiceover/assets", "voiceover/assets")]  # шрифт Manrope (OFL) и значок
datas += [("voiceover/i18n/*.json", "voiceover/i18n")]  # языки интерфейса
hidden = []
try:  # Whisper необязателен: без него сборка проходит, распознавание просто недоступно
    import faster_whisper  # noqa: F401
    datas += collect_data_files("faster_whisper")      # VAD-модель
    hidden += collect_submodules("faster_whisper")
except ImportError:
    pass
excludes = ["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtPdf", "onnxruntime.training"]
if full:
    hidden += collect_submodules("demucs")
    datas += collect_data_files("demucs")
else:
    excludes += ["torch", "torchaudio", "demucs", "torchvision"]

a = Analysis(["run.py"], pathex=["."], datas=datas, hiddenimports=hidden,
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VoiceOverStudio",
          console=False, upx=False, icon="voiceover/assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="VoiceOverStudio", upx=False)
