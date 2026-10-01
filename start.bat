@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Калькулятор сетки MEXC

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if not errorlevel 1 goto deps
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 --version >nul 2>nul
  if errorlevel 1 goto no_python
  py -3 -m venv --clear .venv
) else (
  where python >nul 2>nul
  if errorlevel 1 goto no_python
  python --version >nul 2>nul
  if errorlevel 1 goto no_python
  python -m venv --clear .venv
)
if errorlevel 1 goto setup_error

:deps
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto setup_error
".venv\Scripts\python.exe" app.py
goto :eof

:setup_error
echo Не удалось подготовить Python. Установите Python 3.10+ и проверьте подключение к интернету.
pause
goto :eof

:no_python
echo Python 3.10+ не найден. Установите Python с python.org, затем запустите start.bat снова.
pause
