@echo off
REM Установка всех необходимых зависимостей для сборки EXE
REM Запустите этот файл один раз перед первой сборкой

echo.
echo ========================================
echo Установка зависимостей для сборки EXE
echo ========================================
echo.

REM Проверяем наличие Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Ошибка: Python не установлен!
    echo Пожалуйста установите Python 3.8+ с https://www.python.org/
    echo И убедитесь, что при установке отмечена опция "Add Python to PATH"
    pause
    exit /b 1
)

echo Python найден, версия:
python --version
echo.

REM Переходим в папку родителя для установки зависимостей проекта
cd ..

echo Установка зависимостей проекта из requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo Ошибка при установке зависимостей!
    pause
    exit /b 1
)

REM Возвращаемся в папку exe_build
cd exe_build

echo.
echo Установка PyInstaller...
python -m pip install PyInstaller

if errorlevel 1 (
    echo Ошибка при установке PyInstaller!
    pause
    exit /b 1
)

echo.
echo ========================================
echo Установка завершена успешно!
echo Теперь вы можете запустить build.bat
echo ========================================
echo.
pause

