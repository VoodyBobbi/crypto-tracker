import os
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# Подключаем исходное Flask-приложение проекта
from app import app as flask_app

if getattr(sys, "frozen", False):
    base_dir = Path(sys._MEIPASS)
else:
    base_dir = root

flask_app.template_folder = str(base_dir / "templates")
flask_app.static_folder = str(base_dir / "static")


def start_server():
    flask_app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()

    for _ in range(80):
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:5000/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Калькулятор сетки MEXC")
    window.resize(1500, 980)

    browser = QWebEngineView()
    browser.setUrl(QUrl("http://127.0.0.1:5000/"))
    window.setCentralWidget(browser)
    window.show()

    sys.exit(app.exec())

