@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === VoiceOver Studio: установка ===
echo.

rem --- Подбираем Python 3.10-3.13: для более новых версий часть библиотек
rem --- (onnxruntime, torch) ещё не собрана. Другие версии Python могут стоять рядом.
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython
echo Используется:
%PY% --version
echo.

rem --- Окружение пересоздаём, если оно собрано другим Python (или старым setup.bat).
rem --- Проверяем по своей отметке: код выхода сломанного .venv\Scripts\python.exe
rem --- бывает нулевым, даже когда его базовый Python удалён.
set "VENVPY="
if exist .venv\vo-python.txt set /p VENVPY=<.venv\vo-python.txt
if /i "%VENVPY%"=="%PY%" goto :venvcheck
if not exist .venv goto :makevenv
echo Папка .venv создана другой версией Python, пересоздаю её...
rmdir /s /q .venv
if exist .venv goto :venvlocked
:makevenv
%PY% -m venv .venv || goto :err
>.venv\vo-python.txt echo %PY%
:venvcheck
.venv\Scripts\python.exe -c "import sys, pip; print('.venv: Python', sys.version.split()[0])" || goto :venvbroken
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
echo.
echo Основные модули установлены: запись, перевод, сведение, экспорт.
echo.

echo Устанавливаю локальные модули: распознавание речи Whisper и перевод без интернета...
rem Ставим по одному пакету: если какой-то не соберётся для вашего Python, остальные всё равно установятся
set "LOCALFAIL="
for /f "usebackq eol=# tokens=*" %%P in ("requirements-whisper.txt") do (
  pip install "%%P" || set "LOCALFAIL=%%P"
)
if defined LOCALFAIL goto :nowhisper
echo Локальные модули установлены.
goto :ai
:nowhisper
echo.
echo Часть локальных модулей установить не удалось - см. сообщения pip выше.
echo Остальное установлено и работает.
:ai
echo.
choice /C YN /M "Установить удаление оригинальных голосов Demucs для дубляжа (2-4 ГБ)"
if errorlevel 2 goto :done
choice /C YN /M "Есть видеокарта NVIDIA (ускорение CUDA)"
if errorlevel 2 goto :torchcpu
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 || goto :err
goto :demucs
:torchcpu
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu || goto :err
:demucs
pip install -r requirements-ai.txt || goto :err
:done
echo.
echo Готово! Запускайте run.bat
pause
exit /b 0

:nopython
echo Не найден подходящий Python: нужна версия от 3.10 до 3.13.
echo Сейчас установлено:
python --version 2>nul
py --list 2>nul
echo.
echo Установите Python 3.12 с https://www.python.org/downloads/
echo в установщике отметьте "Add python.exe to PATH".
echo Более новую версию удалять не нужно: setup.bat сам выберет 3.12.
pause
exit /b 1

:venvbroken
echo.
echo Окружение .venv повреждено. Удалите папку .venv и запустите setup.bat ещё раз.
pause
exit /b 1

:venvlocked
echo.
echo Не удалось удалить папку .venv: закройте программу и окна, которые её используют,
echo удалите папку .venv вручную и запустите setup.bat ещё раз.
pause
exit /b 1

:err
echo.
echo Ошибка установки. См. сообщения выше.
pause
exit /b 1
