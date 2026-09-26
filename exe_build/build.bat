@echo off
REM Скрипт для сборки EXE приложения на Windows
REM Просто запустите этот файл двойным щелчком

echo.
echo ========================================
echo Сборка EXE приложения
echo ========================================
echo.

REM Проверяем наличие Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Ошибка: Python не установлен или не в PATH!
    echo Пожалуйста установите Python и добавьте его в переменную окружения PATH.
    pause
    exit /b 1
)

REM Проверяем наличие PyInstaller
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller не установлен. Устанавливаю...
    python -m pip install PyInstaller
    if errorlevel 1 (
        echo Ошибка при установке PyInstaller!
        pause
        exit /b 1
    )
)

echo.
echo Запуск сборки...
echo.

REM Запускаем скрипт сборки
python build_exe.py

if errorlevel 1 (
    echo.
    echo Ошибка при сборке!
    pause
    exit /b 1
)

echo.
echo ========================================
echo Сборка завершена успешно!
echo Готовый EXE находится в папке release/
echo ========================================
echo.
pause

