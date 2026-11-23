# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: pyinstaller voiceover.spec
#   VO_FULL=1  — включить PyTorch/Demucs
#   VO_STORE=1 — сборка для магазинов (Steam): только компоненты, которые можно поставлять
#                с платной программой. Перед ней: python tools/fetch_ffmpeg.py (LGPL FFmpeg)
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, str(Path("tools").resolve()))
import licenses as third_party  # noqa: E402  (tools/licenses.py)

full = os.environ.get("VO_FULL") == "1"
store = os.environ.get("VO_STORE") == "1"
ffmpeg_exe = Path("ffmpeg/bin/ffmpeg.exe")

datas = [("voiceover/assets", "voiceover/assets")]       # шрифт Manrope (OFL) и значок
datas += [("voiceover/i18n/*.json", "voiceover/i18n")]   # языки интерфейса
datas += [("licenses", "licenses")]                      # тексты лицензий, THIRD_PARTY.md
binaries = []
hidden = []
excludes = ["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtPdf", "onnxruntime.training"]

if store:
    if not ffmpeg_exe.is_file():
        raise SystemExit("VO_STORE=1: нет ffmpeg/bin/ffmpeg.exe — сначала python tools/fetch_ffmpeg.py")
    # imageio-ffmpeg несёт GPL-сборку FFmpeg, PyAV — x264/x265 (GPL); в Steam-версию не кладём
    excludes += ["imageio_ffmpeg", "av"]
    flag = Path("build/store_edition")
    flag.parent.mkdir(exist_ok=True)
    flag.write_text("store\n", encoding="utf-8")
    datas += [(str(flag), "voiceover")]                  # см. voiceover/edition.py
else:
    datas += collect_data_files("imageio_ffmpeg", include_py_files=False)

if ffmpeg_exe.is_file():
    binaries += [(str(ffmpeg_exe), "ffmpeg/bin")]
    datas += [(str(p), "licenses/ffmpeg") for p in Path("ffmpeg").iterdir() if p.is_file()]

try:  # Whisper необязателен: без него сборка проходит, распознавание просто недоступно
    import faster_whisper  # noqa: F401
    datas += collect_data_files("faster_whisper")      # VAD-модель
    hidden += collect_submodules("faster_whisper")
except ImportError:
    pass
if full:
    hidden += collect_submodules("demucs")
    datas += collect_data_files("demucs")
else:
    excludes += ["torch", "torchaudio", "demucs", "torchvision"]

a = Analysis(["run.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=excludes, noarchive=False)
# лицензии всех Python-пакетов, которые реально попали в сборку
_lic = third_party.collect([name for name, *_ in a.pure], Path("build/licenses_packages"))
a.datas += Tree(str(_lic), prefix="licenses/packages")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VoiceOverStudio",
          console=False, upx=False, icon="voiceover/assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="VoiceOverStudio", upx=False)
