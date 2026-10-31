@echo off
rem Double-click this file on Windows to open the DevMind web app.
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" -m devmind serve --open --port 8787
if errorlevel 1 pause
endlocal
