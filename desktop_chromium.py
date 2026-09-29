import sys
import os
import time
import shutil
import socket
import threading
import traceback
import urllib.parse

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception:
        pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --disable-gpu-compositing --disable-gpu-sandbox "
    "--disable-accelerated-2d-canvas --no-sandbox "
    "--disable-features=VizDisplayCompositor"
)
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

from PyQt5.QtCore import QUrl, Qt, QTimer
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette, QPixmap
from PyQt5.QtWidgets import QApplication, QMainWindow, QSplashScreen, QFileDialog, QMessageBox
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineDownloadItem

PORT = 5000
URL = f"http://127.0.0.1:{PORT}"
MAX_RESTARTS = 5


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_persistent_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def start_flask():
    from app import app, socketio
    socketio.run(app, host="127.0.0.1", port=PORT, debug=False,
                 allow_unsafe_werkzeug=True, use_reloader=False)


def wait_for_flask(timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


class BrowserPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self._main_window = parent

    def createWindow(self, _type):
        return BrowserPage(self.profile(), self._main_window)

    def javaScriptConsoleMessage(self, level, message, line, source):
        pass

    def chooseFiles(self, mode, old_files, accepted_filters):
        filters = [
            "Audio (*.mp3 *.flac *.wav *.m4a *.ogg *.aac *.wma)",
            "Todos los archivos (*)"
        ]
        files, _ = QFileDialog.getOpenFileNames(
            self._main_window,
            "Seleccionar archivo de audio",
            os.path.expanduser("~\\Music"),
            " ;; ".join(filters)
        )
        return files


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BPMSTART DOWNLOADER")
        self.setMinimumSize(900, 650)
        self.resize(1100, 750)
        self._restart_count = 0
        self._flask_ok = False
        self._crash_shown = False
        self._last_url = URL

        base_dir = get_base_dir()
        icon_path = os.path.join(base_dir, "static", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.browser = QWebEngineView(self)
        self.setCentralWidget(self.browser)

        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        profile.downloadRequested.connect(self._on_download)

        page = BrowserPage(profile, self)
        self.browser.setPage(page)
        page.renderProcessTerminated.connect(self._on_crash)

        self.apply_dark_style()

    def load_flask(self):
        if wait_for_flask(timeout=15):
            self._flask_ok = True
            self._last_url = URL
            self.browser.load(QUrl(URL))
        else:
            QMessageBox.critical(self, "Error", "El servidor no pudo iniciar.\nCierra y vuelve a intentarlo.")

    def _on_crash(self, status, exit_code):
        self._restart_count += 1
        if self._restart_count <= MAX_RESTARTS:
            self.browser.page().deleteLater()
            profile = QWebEngineProfile.defaultProfile()
            page = BrowserPage(profile, self)
            self.browser.setPage(page)
            page.renderProcessTerminated.connect(self._on_crash)
            QTimer.singleShot(2000, self._reload_after_crash)
        elif not self._crash_shown:
            self._crash_shown = True
            QMessageBox.critical(self, "Error", "El navegador se ha cerrado varias veces.\nReinicia el programa.")

    def _reload_after_crash(self):
        if self._flask_ok and wait_for_flask(timeout=5):
            self.browser.load(QUrl(self._last_url))
        else:
            self.browser.load(QUrl(self._last_url))

    def _on_download(self, download_item):
        suggested = download_item.suggestedFileName()
        if not suggested:
            path = download_item.url().path()
            suggested = os.path.basename(urllib.parse.unquote(path))

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Guardar archivo",
            os.path.join(os.path.expanduser("~"), "Downloads", suggested),
            "Todos los archivos (*)"
        )
        if save_path:
            download_item.setDownloadDirectory(os.path.dirname(save_path))
            download_item.setDownloadFileName(os.path.basename(save_path))
            download_item.accept()
        else:
            download_item.cancel()

    def apply_dark_style(self):
        self.setStyleSheet("QMainWindow { background-color: #0a0a0f; } QWebEngineView { background-color: #0a0a0f; }")
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#0a0a0f"))
        palette.setColor(QPalette.WindowText, QColor("#f0f0f5"))
        self.setPalette(palette)

    def closeEvent(self, event):
        self.browser.stop()
        QApplication.quit()


def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    app_qt = QApplication(sys.argv)
    app_qt.setApplicationName("BPMSTART DOWNLOADER")

    base_dir = get_base_dir()
    icon_path = os.path.join(base_dir, "static", "icon.ico")
    if os.path.exists(icon_path):
        app_qt.setWindowIcon(QIcon(icon_path))

    splash_pix = QPixmap(400, 250)
    splash_pix.fill(QColor("#0a0a0f"))
    splash = QSplashScreen(splash_pix)
    splash.showMessage("Iniciando BPMSTART DOWNLOADER...", Qt.AlignCenter | Qt.AlignBottom, QColor("#ff4444"))
    splash.setFont(QFont("Segoe UI", 11))
    splash.show()

    window = MainWindow()

    def open_main():
        splash.close()
        window.show()
        window.load_flask()

    QTimer.singleShot(1500, open_main)
    sys.exit(app_qt.exec_())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Ocurrio un error. Presiona Enter para salir...")
