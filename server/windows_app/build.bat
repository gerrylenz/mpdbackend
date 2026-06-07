@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python generate_icon.py
if errorlevel 1 exit /b 1

python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "MPD-Player" ^
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

echo.
echo Fertig: dist\MPD-Player.exe
echo Einstellungen: %%APPDATA%%\mpdbackend-player\config.json
echo Taskleiste: Rechtsklick auf X minimiert ins Tray
echo Autostart: Einstellungen oder Tray-Menue
