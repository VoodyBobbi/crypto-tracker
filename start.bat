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

".venv\Scripts\python.exe" -c "from urllib.request import urlopen; page=urlopen('http://127.0.0.1:5000/', timeout=2).read(); raise SystemExit(0 if b'<title>' in page and b'MEXC' in page else 1)" >nul 2>nul
if not errorlevel 1 goto open_site

netstat -ano | findstr /C:"127.0.0.1:5000" | findstr /C:"LISTENING" >nul
if not errorlevel 1 goto port_busy

start "" /b ".venv\Scripts\pythonw.exe" "%~dp0app.py"
for /l %%i in (1,1,20) do (
    ".venv\Scripts\python.exe" -c "from urllib.request import urlopen; page=urlopen('http://127.0.0.1:5000/', timeout=1).read(); raise SystemExit(0 if b'<title>' in page and b'MEXC' in page else 1)" >nul 2>nul
    if not errorlevel 1 goto open_site
    timeout /t 1 /nobreak >nul
)
echo The MEXC app did not become available at http://127.0.0.1:5000.
pause
goto :eof

:open_site
start "" "http://127.0.0.1:5000"
goto :eof

:port_busy
echo Port 5000 is already in use by another application.
echo Close that application, then run start.bat again.
pause
goto :eof

:setup_error
echo Could not prepare Python or install dependencies.
pause
goto :eof

:no_python
echo Python 3.10 or newer was not found. Install Python, then run start.bat again.
pause
