import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop_app" / "dist"
WORK = ROOT / "desktop_app" / "build"

cmd = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "GridCalcDesktop",
    "--workpath",
    str(WORK),
    "--distpath",
    str(OUT),
    str(ROOT / "desktop_app" / "main.py"),
]

print("Запуск сборки...")
result = subprocess.run(cmd, cwd=str(ROOT))
if result.returncode == 0:
    print("Готово. EXE: ", OUT / "GridCalcDesktop.exe")
else:
    print("Ошибка сборки")
    raise SystemExit(result.returncode)

