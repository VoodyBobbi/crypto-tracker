#!/usr/bin/env python
"""
Скрипт для сборки EXE приложения из Flask проекта используя PyInstaller.
Запуск: python build_exe.py
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# Папки
PROJECT_ROOT = Path(__file__).parent.parent
BUILD_FOLDER = PROJECT_ROOT / "exe_build" / "build"
DIST_FOLDER = PROJECT_ROOT / "exe_build" / "dist"

def clean_build():
    """Очищает старые артефакты сборки"""
    print("🗑️  Очистка старых файлов сборки...")
    for folder in [BUILD_FOLDER, DIST_FOLDER, PROJECT_ROOT / "build", PROJECT_ROOT / "dist"]:
        if folder.exists():
            shutil.rmtree(folder)

    spec_file = PROJECT_ROOT / "app.spec"
    if spec_file.exists():
        spec_file.unlink()

def build_exe():
    """Собирает EXE файл используя PyInstaller"""
    print("\n🔨 Сборка EXE файла...")

    spec_file = PROJECT_ROOT / "exe_build" / "app.spec"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec_file),
        "--workpath", str(BUILD_FOLDER),
        "--distpath", str(DIST_FOLDER),
    ]

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode == 0

def copy_final_exe():
    """Копирует готовый EXE в папку exe_build/release"""
    print("\n📦 Подготовка финального релиза...")

    release_folder = PROJECT_ROOT / "exe_build" / "release"
    release_folder.mkdir(exist_ok=True)

    exe_file = DIST_FOLDER / "КалькуляторСетки.exe"
    if exe_file.exists():
        dest = release_folder / "КалькуляторСетки.exe"
        shutil.copy2(exe_file, dest)
        print(f"✅ EXE скопирован: {dest}")
        return True
    else:
        print(f"❌ Ошибка: EXE файл не найден в {DIST_FOLDER}")
        return False

def main():
    try:
        print("=" * 60)
        print("🚀 Сборка EXE приложения для Калькулятора Сетки MEXC")
        print("=" * 60)

        clean_build()

        if not build_exe():
            print("❌ Сборка EXE не удалась!")
            sys.exit(1)

        if copy_final_exe():
            print("\n" + "=" * 60)
            print("✅ Сборка завершена успешно!")
            print("📍 Готовый файл находится в: exe_build/release/")
            print("=" * 60)
            return 0
        else:
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Ошибка при сборке: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    sys.exit(main())

