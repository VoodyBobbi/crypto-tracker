@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Калькулятор сетки MEXC
set FIRST=

if exist ".venv\Scripts\python.exe" goto deps

echo Первый запуск: готовлю окружение, это минута...
python -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" goto nopython
set FIRST=1

:deps
rem Каждый запуск сверяет библиотеки: если проект обновился, доставит новое.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 if defined FIRST goto nopip

".venv\Scripts\python.exe" app.py
pause
goto :eof

:nopython
echo.
echo Python не найден.
echo Скачайте с python.org и при установке отметьте "Add python.exe to PATH".
pause
goto :eof

:nopip
echo.
echo Не удалось установить библиотеки. Проверьте интернет и запустите снова.
rmdir /s /q .venv
pause
