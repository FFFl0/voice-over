@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка VoiceOver Studio.exe (папка dist\VoiceOverStudio).
rem   build.bat        — лёгкая сборка без PyTorch (запись, экспорт)
rem   build.bat full   — полная сборка с PyTorch/Demucs (если они установлены в .venv)
rem   build.bat steam  — полная сборка для Steam: LGPL FFmpeg, без NLLB и PyAV (см. steam\README.md)
call .venv\Scripts\activate.bat || (echo Сначала запустите setup.bat & pause & exit /b 1)
pip install pyinstaller || exit /b 1
set VO_FULL=0
set VO_STORE=0
if /I "%1"=="full" set VO_FULL=1
if /I "%1"=="steam" (
  set VO_FULL=1
  set VO_STORE=1
  if not exist ffmpeg\bin\ffmpeg.exe (python tools\fetch_ffmpeg.py || (pause & exit /b 1))
)
pyinstaller --noconfirm voiceover.spec || (pause & exit /b 1)
echo.
echo Готово: dist\VoiceOverStudio\VoiceOverStudio.exe
pause
