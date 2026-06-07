@echo off
setlocal
cd /d "%~dp0"

set "PROJECT_DIR=%~dp0"
set "FINAL_DIST=%PROJECT_DIR%dist"
set "BUILD_ROOT=%LOCALAPPDATA%\mpdbackend-player-build"
set "BUILD_WORK=%BUILD_ROOT%\build"
set "BUILD_DIST=%BUILD_ROOT%\dist"

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python generate_icon.py
if errorlevel 1 exit /b 1

tasklist /FI "IMAGENAME eq MPD-Player.exe" 2>nul | find /I "MPD-Player.exe" >nul
if not errorlevel 1 (
  echo.
  echo FEHLER: MPD-Player.exe laeuft noch. Bitte im Tray beenden, dann erneut bauen.
  exit /b 1
)

if not exist "%BUILD_ROOT%" mkdir "%BUILD_ROOT%"
if not exist "%BUILD_WORK%" mkdir "%BUILD_WORK%"
if not exist "%BUILD_DIST%" mkdir "%BUILD_DIST%"
if not exist "%FINAL_DIST%" mkdir "%FINAL_DIST%"

echo.
echo Build auf lokalem Laufwerk: %BUILD_ROOT%
echo ^(vermeidet PyInstaller-Fehler auf Netzlaufwerken^)
echo.

python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "MPD-Player" ^
  --workpath "%BUILD_WORK%" ^
  --distpath "%BUILD_DIST%" ^
  --icon "assets\icon.ico" ^
  --add-data "assets\icon.ico;assets" ^
  --add-data "assets\icon.png;assets" ^
  --hidden-import webview ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import pystray._win32 ^
  --exclude-module webview.platforms.android ^
  --exclude-module webview.platforms.cocoa ^
  --exclude-module webview.platforms.gtk ^
  --exclude-module webview.platforms.qt ^
  --exclude-module webview.platforms.cef ^
  mpd_player.py
if errorlevel 1 exit /b 1

if not exist "%BUILD_DIST%\MPD-Player.exe" (
  echo FEHLER: EXE nicht gefunden in %BUILD_DIST%
  exit /b 1
)

copy /Y "%BUILD_DIST%\MPD-Player.exe" "%FINAL_DIST%\MPD-Player.exe" >nul
if errorlevel 1 (
  echo FEHLER: Konnte EXE nicht nach %FINAL_DIST% kopieren.
  exit /b 1
)

echo.
echo Fertig: %FINAL_DIST%\MPD-Player.exe
echo Zwischendateien: %BUILD_ROOT%
echo Einstellungen: %%APPDATA%%\mpdbackend-player\config.json
echo Taskleiste: Rechtsklick auf X minimiert ins Tray
echo Autostart: Einstellungen oder Tray-Menue
