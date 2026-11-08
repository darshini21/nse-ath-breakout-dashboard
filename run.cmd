@echo off
setlocal
set VENV=%LOCALAPPDATA%\nse-breakout-venv
set PY=%VENV%\Scripts\python.exe
cd /d "%~dp0"

if "%1"=="setup" (
  if not exist "%PY%" (
    where py >nul 2>nul && (py -3 -m venv "%VENV%") || ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv "%VENV%")
  )
  "%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
  "%PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
  echo Setup done. Now copy .env.example to .env and fill in your Angel One values.
  goto :eof
)
if not exist "%PY%" (
  echo Virtualenv not found. Run:  run.cmd setup
  exit /b 1
)
if "%1"=="test" (
  "%PY%" -m tests.test_screener
  goto :eof
)
if "%1"=="" (
  echo Usage: run.cmd setup ^| universe ^| history ^| screen ^| serve ^| all ^| test
  goto :eof
)
"%PY%" -m app.cli %*
