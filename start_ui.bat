@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv-ui\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)"
  if not errorlevel 1 (
    py -3 -m venv .venv-ui
    goto environment_ready
  )
)
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto no_python
python -m venv .venv-ui
:environment_ready
if not exist ".venv-ui\Scripts\python.exe" goto failed
:dependencies
".venv-ui\Scripts\python.exe" scripts\prepare_ui_environment.py
if errorlevel 1 goto failed
".venv-ui\Scripts\python.exe" scripts\start_ui.py %*
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Python 3.11 or newer was not found. Install Python from python.org and enable PATH.
pause
exit /b 1
:failed
echo Startup failed. Check the message above. Internet is needed for first dependency installation.
echo No administrator privileges or PyCharm are needed.
pause
exit /b 1
