@echo off
rem Run DevMind from PowerShell or Command Prompt without activating a venv:
rem   .\devmind.bat setup --kind ollama --write
rem   .\devmind.bat doctor
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo DevMind is not installed in this folder yet.
  echo Double-click START-DEVMIND.bat once ^(it creates .venv^), then try again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m devmind %*
set "CODE=%ERRORLEVEL%"
endlocal & exit /b %CODE%
