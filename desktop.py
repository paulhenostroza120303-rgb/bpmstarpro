import sys
import os
import time
import shutil
import socket
import threading
import traceback

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception:
        pass

os.environ.setdefault("COMPLUS_Version", "v4.0.30319")
os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disable-gpu")

PORT = 5000
URL = "http://127.0.0.1:%s" % PORT


def get_free_port():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
    except Exception:
        return PORT


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_persistent_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_log_path():
    return os.path.join(get_persistent_dir(), "error.log")


def log(msg):
    try:
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def show_error_box(message, title="BPMStartPro"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        try:
            input(message + "\n")
        except Exception:
            pass


def unblock_file(path):
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.DeleteFileW(path + ":Zone.Identifier"))
    except Exception:
        return False


def unblock_directory(root):
    removed = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            for fname in filenames:
                if unblock_file(os.path.join(dirpath, fname)):
                    removed += 1
    except Exception:
        pass
    return removed


def start_flask():
    try:
        from app import app, socketio
        log("Arrancando servidor Flask en puerto %s" % PORT)
        socketio.run(app, host="127.0.0.1", port=PORT, debug=False,
                     allow_unsafe_werkzeug=True, use_reloader=False)
    except Exception as e:
        log("[Flask] Error: %s" % e)
        log("[Flask] Traceback: %s" % traceback.format_exc())


def wait_for_flask(timeout=15):
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(URL + "/", timeout=1) as r:
                body = r.read(2048).decode("utf-8", "ignore")
                if "BPMSTART" in body:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


WEBVIEW2_CLIENTS = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_REG_PATHS = [
    (0x80000002, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    (0x80000002, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    (0x80000001, r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
]


def is_webview2_installed():
    try:
        import winreg
    except Exception:
        return True
    for hive, path in WEBVIEW2_REG_PATHS:
        try:
            key = winreg.OpenKey(hive, path)
            try:
                value, _ = winreg.QueryValueEx(key, "pv")
                if value and str(value).strip() not in ("", "0.0.0.0"):
                    return True
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return False


def find_bootstrapper():
    candidates = [
        os.path.join(get_base_dir(), "bin", "MicrosoftEdgeWebview2Setup.exe"),
        os.path.join(get_persistent_dir(), "MicrosoftEdgeWebview2Setup.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def get_webview2_version():
    try:
        import winreg
    except Exception:
        return "desconocido"
    for hive, path in WEBVIEW2_REG_PATHS:
        try:
            key = winreg.OpenKey(hive, path)
            try:
                value, _ = winreg.QueryValueEx(key, "pv")
                if value and str(value).strip() not in ("", "0.0.0.0"):
                    return str(value)
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return "no instalado"


def clear_webview_data():
    path = os.path.join(get_persistent_dir(), "webview_data")
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            log("Datos de WebView2 limpiados (webview_data)")
    except Exception as e:
        log("No se pudo limpiar webview_data: %s" % e)


def webview2_es_vieja():
    version = get_webview2_version()
    try:
        num = int(str(version).split(".")[0])
        return num < 140
    except Exception:
        return False


def find_msedge():
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


FIXED_RUNTIME_VERSION = "150.0.4078.105"
FIXED_RUNTIME_URL = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
    "b401c036-cfb8-4dc4-a58e-8766441df4ac/"
    "Microsoft.WebView2.FixedVersionRuntime.150.0.4078.105.x64.cab"
)


def system_webview2_ok():
    version = get_webview2_version()
    if version in ("desconocido", "no instalado"):
        return False
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "EdgeWebView", "Application", version, "msedgewebview2.exe"))
    for path in candidates:
        if os.path.exists(path):
            return True
    log("WebView2 del sistema parece rota (version %s registrada pero sin archivos)" % version)
    return False


def get_local_runtime_dir():
    return os.path.join(
        get_persistent_dir(),
        "edgewebview2",
        "Microsoft.WebView2.FixedVersionRuntime.%s.x64" % FIXED_RUNTIME_VERSION,
    )


def ensure_local_runtime():
    runtime_dir = get_local_runtime_dir()
    if os.path.exists(os.path.join(runtime_dir, "msedgewebview2.exe")):
        return runtime_dir
    return None


def download_fixed_runtime():
    import subprocess as sp
    cab = os.path.join(get_persistent_dir(), "edgewebview2_download.cab")
    target = os.path.join(get_persistent_dir(), "edgewebview2")
    try:
        import urllib.request
        log("Descargando runtime WebView2 (%s MB aprox)..." % FIXED_RUNTIME_VERSION)
        urllib.request.urlretrieve(FIXED_RUNTIME_URL, cab)
        log("Descarga de runtime completada, extrayendo...")
        os.makedirs(target, exist_ok=True)
        sp.run(["expand.exe", cab, "-F:*", target], check=True)
        os.remove(cab)
        runtime_dir = get_local_runtime_dir()
        if os.path.exists(os.path.join(runtime_dir, "msedgewebview2.exe")):
            log("Runtime WebView2 listo en %s" % runtime_dir)
            return runtime_dir
        log("Runtime extraido pero no se encontro msedgewebview2.exe")
        return None
    except Exception as e:
        log("Error descargando runtime WebView2: %s" % e)
        try:
            if os.path.exists(cab):
                os.remove(cab)
        except Exception:
            pass
        return None


def try_install_webview2():
    bootstrapper = find_bootstrapper()
    if not bootstrapper:
        log("Instalador WebView2 no encontrado")
        return False
    log("WebView2 Runtime faltante, instalando...")
    try:
        import subprocess
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(
            [bootstrapper, "/silent", "/install"],
            timeout=300, startupinfo=startupinfo,
        )
        log("Instalador WebView2 termino con codigo %s" % proc.returncode)
        return is_webview2_installed()
    except Exception as e:
        log("Error instalando WebView2: %s" % e)
        return False


class Api:
    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def save_file(self, server_path, default_name):
        try:
            import webview
            from urllib.parse import unquote
            from app import SEPARATIONS_DIR, DOWNLOADS_DIR

            server_path = unquote(server_path)

            if server_path.startswith("/download_stem/"):
                relative = server_path[len("/download_stem/"):]
                full_path = SEPARATIONS_DIR / relative
            elif server_path.startswith("/download_file/"):
                relative = server_path[len("/download_file/"):]
                full_path = DOWNLOADS_DIR / relative
            else:
                return {"error": "Ruta no valida"}

            if not full_path.exists():
                return {"error": "Archivo no encontrado: %s" % full_path}

            # Detectar extension para filtro apropiado
            ext = full_path.suffix.lower()
            if ext == ".mp4":
                file_types = ("Video (*.mp4;*.mkv;*.avi)",)
            elif ext in (".mid", ".midi"):
                file_types = ("Archivos MIDI (*.mid;*.midi)", "Todos los archivos (*.*)")
            else:
                file_types = ("Audio (*.mp3;*.wav;*.flac;*.m4a;*.ogg;*.aac)", "Todos los archivos (*.*)")

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=default_name,
                file_types=file_types,
            )

            if not result:
                return {"error": "Cancelado"}

            save_path = result if isinstance(result, str) else result[0]
            shutil.copy2(str(full_path), save_path)
            return {"ok": True, "path": save_path}
        except Exception as e:
            return {"error": str(e)}


api = Api()


def open_window(runtime_path=None):
    import webview

    if runtime_path:
        webview.settings['WEBVIEW2_RUNTIME_PATH'] = runtime_path
        log("Usando runtime WebView2 incluido: %s" % runtime_path)

    window = webview.create_window(
        "BPMSTART DOWNLOADER",
        URL,
        width=1100,
        height=750,
        min_size=(900, 650),
        text_select=True,
        js_api=api,
    )
    api.set_window(window)

    state = {"loaded": False}

    def _on_loaded():
        state["loaded"] = True
        log("Pagina cargada en la ventana")

    window.events.loaded += _on_loaded

    def _retry_load():
        for i in range(12):
            time.sleep(5)
            if state["loaded"]:
                return
            log("La pagina no cargo, recargando (intento %s)..." % (i + 1))
            try:
                window.load_url(URL)
            except Exception as e:
                log("Error recargando la pagina: %s" % e)

        if state["loaded"]:
            return

        log("La pagina no cargo despues de todos los intentos")
        try:
            diag = window.evaluate_js(
                "document.title + '|' + (document.body ? document.body.innerHTML.length : 'sinbody')"
            )
            log("Diagnostico de la pagina: %s" % diag)
        except Exception as e:
            log("Diagnostico de la pagina fallo: %s" % e)

        import subprocess
        edge = find_msedge()
        if edge:
            log("Abriendo la interfaz en Edge (modo app)")
            try:
                proc = subprocess.Popen([edge, "--app=" + URL, "--disable-gpu"])
                proc.wait()
                log("Se cerro la ventana de Edge")
            except Exception as e:
                log("Error abriendo Edge: %s" % e)
        else:
            log("Edge no encontrado en este equipo")

        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=_retry_load, daemon=True).start()

    webview.start(debug=False, storage_path=os.path.join(get_persistent_dir(), "webview_data"))
    return True


def setup_logging():
    try:
        import logging
        handler = logging.FileHandler(get_log_path(), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        log("Logging interno activado")
    except Exception as e:
        log("Error configurando logging interno: %s" % e)


def main():
    log("Iniciando BPMStartPro")
    setup_logging()

    removed = unblock_directory(get_base_dir()) + unblock_directory(get_persistent_dir())
    if removed:
        log("Marcas de archivos descargados eliminadas: %s" % removed)

    global PORT, URL
    PORT = get_free_port()
    URL = "http://127.0.0.1:%s" % PORT
    log("Usando puerto %s" % PORT)

    try:
        import platform
        log("Windows: %s" % platform.platform())
    except Exception:
        pass
    log("WebView2: %s" % get_webview2_version())
    log("Edge: %s" % ("instalado" if find_msedge() else "no instalado"))

    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    if not wait_for_flask(timeout=20):
        log("El servidor tardo en responder, la ventana intentara recargar")
    else:
        log("Servidor listo")

    runtime_path = None
    if not system_webview2_ok():
        log("El WebView2 del sistema no es utilizable, se usara un runtime propio")
        runtime_path = ensure_local_runtime()
        if not runtime_path:
            log("Runtime WebView2 no descargado aun, descargando...")
            show_error_box(
                "Primera ejecucion: BPMStartPro esta descargando un componente necesario.\n"
                "Puede tomar de 1 a 5 minutos segun tu conexion.\n"
                "No cierres el programa hasta que se abra la ventana."
            )
            runtime_path = download_fixed_runtime()
        if not runtime_path:
            log("No se pudo obtener el runtime WebView2, la ventana podria no abrir")
    else:
        log("WebView2 del sistema funcional: %s" % get_webview2_version())

    clear_webview_data()

    try:
        open_window(runtime_path)
        return
    except Exception as e:
        log("[pywebview] Error al iniciar: %s" % e)
        traceback.print_exc()

    removed = unblock_directory(get_base_dir()) + unblock_directory(get_persistent_dir())
    if removed:
        log("Marcas de archivos descargados eliminadas en reintento: %s" % removed)

    try:
        open_window(runtime_path)
        return
    except Exception as e2:
        log("[pywebview] Error al reintentar: %s" % e2)
        traceback.print_exc()

    show_error_box(
        "No se pudo abrir la ventana de BPMStartPro.\n"
        "Revisa el archivo error.log junto al programa.\n"
        "Si el error persiste, reinstala el programa."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        log("Error fatal: %s" % traceback.format_exc())
        input("Ocurrio un error. Presiona Enter para salir...")
