@echo off
rem ===========================================================================
rem  DevMind - double-click launcher for Windows
rem  Finds Python, creates .venv, installs DevMind once, starts the web app.
rem  If anything fails the window stays open and shows what to do.
rem
rem  Diagnostics without starting the server:
rem      set DEVMIND_LAUNCH_DRY_RUN=1
rem      scripts\devmind.bat
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
title DevMind
cd /d "%~dp0.."
if errorlevel 1 goto :no_folder

set "LOG=%TEMP%\devmind-launch.log"
if "%TEMP%"=="" set "LOG=%USERPROFILE%\devmind-launch.log"
break > "%LOG%" 2>nul
set "PORT=8787"
if not "%DEVMIND_PORT%"=="" set "PORT=%DEVMIND_PORT%"
set "DRY=0"
if "%DEVMIND_LAUNCH_DRY_RUN%"=="1" set "DRY=1"
set "DEMO="
if "%DEVMIND_DEMO%"=="1" set "DEMO=--demo"

set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
set "PY="

if exist "%VENV_PY%" (
  set "PY=%VENV_PY%"
  goto :have_python
)

rem --- find a usable Python 3.11+ -------------------------------------------
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python3"
)
if not defined PY goto :no_python
if not "%DRY%"=="1" (
  echo First run: creating .venv with %PY% ...
  %PY% -m venv .venv >>"%LOG%" 2>&1
  if errorlevel 1 goto :venv_failed
  set "PY=%VENV_PY%"
)

:have_python
if "%DRY%"=="1" (
  echo root=%CD%
  echo python=%PY%
  echo command=%PY% -m devmind serve --open --port %PORT% %DEMO%
  goto :end
)

"%PY%" -c "import fastapi, httpx, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo Installing DevMind and its dependencies - one time, needs internet ...
  "%PY%" -m pip install --disable-pip-version-check -e . >>"%LOG%" 2>&1
  if errorlevel 1 goto :install_failed
)

rem --- pick a nearby free port ----------------------------------------------
set "FREEPORT="
for /l %%P in (%PORT%,1,8807) do (
  if not defined FREEPORT (
    "%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(0 if s.connect_ex(('127.0.0.1',%%P)) else 1)" >nul 2>&1
    if not errorlevel 1 set "FREEPORT=%%P"
  )
)
if not defined FREEPORT goto :no_port
set "PORT=%FREEPORT%"

echo Starting DevMind at http://127.0.0.1:%PORT%
echo Leave this window open while you use the app. Press Ctrl+C to stop it.
echo.
"%PY%" -m devmind serve --open --port %PORT% %DEMO%
if errorlevel 1 goto :server_failed
goto :end

:no_folder
echo.
echo DevMind could not find its own folder.
echo Keep the whole project together and double-click scripts\devmind.bat again.
goto :fail

:no_python
echo.
echo DevMind needs Python 3.11 or newer, and it is not installed (or not on PATH).
echo.
echo   1. Download it from https://www.python.org/downloads/windows/
echo   2. On the first installer screen tick "Add python.exe to PATH".
echo   3. Click Install Now, then double-click this file again.
echo.
start "" "https://www.python.org/downloads/windows/"
goto :fail

:venv_failed
echo.
echo Could not create the .venv folder. Read the end of:
echo   %LOG%
echo Usually this means Python is installed but incomplete, or antivirus blocked it.
goto :fail

:install_failed
echo.
echo Installing DevMind failed. Common causes:
echo   * no internet access right now,
echo   * a company proxy - set HTTPS_PROXY and try again,
echo   * antivirus blocking pip.
echo Log: %LOG%
goto :fail

:no_port
echo.
echo There is no free port between %PORT% and 8807.
echo Close the other DevMind window (check the taskbar) and try again.
goto :fail

:server_failed
echo.
echo DevMind stopped with an error. The last lines are above; the full log is:
echo   %LOG%
echo The most common cause is that the model server is not running:
echo   start llama.cpp (or set [backend] kind = "ollama") and try again.
goto :fail

:fail
echo.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
