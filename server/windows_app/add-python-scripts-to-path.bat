@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add-python-scripts-to-path.ps1" %*
exit /b %ERRORLEVEL%
