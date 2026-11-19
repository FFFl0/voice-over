@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === VoiceOver Studio: установка ===
echo.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version || (
  echo Python не найден. Установите Python 3.11 или 3.12 с https://www.python.org
  echo и отметьте "Add python.exe to PATH".
  pause & exit /b 1
)
if not exist .venv (
  %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
echo.
echo Основные модули установлены (Edge TTS, запись, экспорт).
echo.
choice /C YN /M "Установить AI-модули: офлайн-голоса Silero и удаление голосов Demucs (2-4 ГБ)"
if errorlevel 2 goto :done
choice /C YN /M "Есть видеокарта NVIDIA (ускорение CUDA)"
if errorlevel 2 (
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu || goto :err
) else (
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 || goto :err
)
pip install -r requirements-ai.txt || goto :err
:done
echo.
echo Готово! Запускайте run.bat
pause
exit /b 0
:err
echo.
echo Ошибка установки. См. сообщения выше.
pause
exit /b 1
