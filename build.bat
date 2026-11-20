@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка VoiceOver Studio.exe (папка dist\VoiceOverStudio).
rem   build.bat        — лёгкая сборка без PyTorch (Edge TTS, запись, экспорт)
rem   build.bat full   — полная сборка с PyTorch/Demucs (если они установлены в .venv)
call .venv\Scripts\activate.bat || (echo Сначала запустите setup.bat & pause & exit /b 1)
pip install pyinstaller || exit /b 1
if /I "%1"=="full" (set VO_FULL=1) else (set VO_FULL=0)
pyinstaller --noconfirm voiceover.spec || (pause & exit /b 1)
echo.
echo Готово: dist\VoiceOverStudio\VoiceOverStudio.exe
pause
