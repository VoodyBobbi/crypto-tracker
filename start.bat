@echo off
setlocal
cd /d "%~dp0"
title MEXC Grid Calculator

if not exist ".venv\Scripts\python.exe" goto make_venv
".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if not errorlevel 1 goto deps

:make_venv
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 --version >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m venv --clear .venv
if errorlevel 1 goto setup_error
goto deps

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python --version >nul 2>nul
if errorlevel 1 goto no_python
python -m venv --clear .venv
if errorlevel 1 goto setup_error

:deps
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto setup_error
".venv\Scripts\python.exe" app.py
goto :eof

:setup_error
echo Could not prepare Python or install dependencies.
pause
goto :eof

:no_python
echo Python 3.10 or newer was not found. Install Python, then run start.bat again.
pause
