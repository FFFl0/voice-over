@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
rem Загрузка сборки в Steam (SteamPipe). Сначала: build.bat steam.
rem   steam\upload.bat APP_ID DEPOT_ID STEAM_ЛОГИН
rem steamcmd — из Steamworks SDK (sdk\tools\ContentBuilder\builder\steamcmd.exe);
rem путь можно задать переменной STEAMCMD.
if "%~3"=="" (echo Использование: steam\upload.bat APP_ID DEPOT_ID STEAM_ЛОГИН & exit /b 1)
if "%STEAMCMD%"=="" set STEAMCMD=steamcmd.exe
if not exist dist\VoiceOverStudio\VoiceOverStudio.exe (echo Нет сборки — запустите build.bat steam & exit /b 1)
call .venv\Scripts\activate.bat 2>nul
python steam\make_vdf.py %1 %2 || exit /b 1
"%STEAMCMD%" +login %3 +run_app_build "%cd%\build\steam\app_build_%1.vdf" +quit
if errorlevel 1 (echo Загрузка не удалась & exit /b 1)
echo.
echo Сборка загружена. Выпустить её: Steamworks → SteamPipe → Сборки → выбрать ветку.
