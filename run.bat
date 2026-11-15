@echo off
REM One-click start on Windows. Creates a virtual environment on first run.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto start
echo Setting up the Python environment - first run only...
python -m venv .venv
if errorlevel 1 goto nopython
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
:start
REM picks up any new requirement after an update (quick when nothing is missing)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python run.py
pause
exit /b 0
:nopython
echo Python 3.10 or newer is required. Download it from https://www.python.org/downloads/
pause
exit /b 1
