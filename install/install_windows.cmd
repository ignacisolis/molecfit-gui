@echo off
rem Doble clic para instalar Molecfit GUI en Windows (via WSL2).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %*
pause
