import os
import re
import sys
import json
import uuid
import time
import threading
import subprocess
import webbrowser
from pathlib import Path
from functools import wraps

import requests
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit

# PyInstaller path detection
if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    persistent_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    persistent_dir = base_dir

# Reset DLL search path on Windows (PyInstaller fix)
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception:
        pass

from binaries import get_ytdlp_path, get_ffmpeg_dir, get_js_runtime_arg


def log(msg):
    try:
        with open(os.path.join(persistent_dir, "error.log"), "a", encoding="utf-8") as f:
            f.write("[%s] [app] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass

APP_VERSION = "2.2.6"
GITHUB_REPO = "paulhenostroza120303-rgb/bpmstarpro"

app = Flask(__name__,
            template_folder=os.path.join(base_dir, 'templates'),
            static_folder=os.path.join(base_dir, 'static'))
app.config["SECRET_KEY"] = "bpmstartpro-secret-2024"
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


def check_for_updates():
    if not GITHUB_REPO or GITHUB_REPO == "tu_usuario/tu_repo":
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {"User-Agent": "BPMStartPro-AutoUpdater"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            tag = data.get("tag_name", "").lstrip("v").strip()
            if tag and tag != APP_VERSION:
                # Search for installer asset
                download_url = None
                for asset in data.get("assets", []):
                    if asset.get("name", "").endswith(".exe"):
                        download_url = asset.get("browser_download_url")
                        break
                return {
                    "has_update": True,
                    "latest_version": tag,
                    "current_version": APP_VERSION,
                    "notes": data.get("body", "Nueva version disponible."),
                    "download_url": download_url,
                }
    except Exception as e:
        log("Error comprobando actualizaciones: %s" % e)
    return None


@app.after_request
def add_no_cache_headers(resp):
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

BASE_DIR = Path(persistent_dir)
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
SEPARATIONS_DIR = BASE_DIR / "separations"
SEPARATIONS_DIR.mkdir(exist_ok=True)
MIDI_PROJECTS_DIR = BASE_DIR / "midi_projects"
MIDI_PROJECTS_DIR.mkdir(exist_ok=True)
PITCH_DIR = BASE_DIR / "tonalidades"
PITCH_DIR.mkdir(exist_ok=True)

# midi_engine arrastra librosa, numba, basic-pitch y onnxruntime: importarlo
# aqui tardaba ~30s y retrasaba el arranque del servidor, dejando la ventana
# en "127.0.0.1 rechazo la conexion" en equipos lentos. Se carga solo cuando
# el usuario usa MIDI Pro (ver load_midi_engine), asi la app abre al instante
# y un fallo de esas librerias ya no impide descargar ni separar.
midi_engine = None


def load_midi_engine():
    global midi_engine
    if midi_engine is None:
        import midi_engine as _me
        midi_engine = _me
    return midi_engine


active_downloads = {}
active_separations = {}

YT_CLIENTS = ["android_creator", "default", "android", "tv", "web_embedded", "ios"]
# El cliente "android" casi nunca expone las pistas de video DASH de mayor
# resolucion (a menudo cae al progresivo viejo de 360p) aunque el audio le
# funcione bien. Para MP4 lo dejamos de ultimo recurso, mientras android_creator
# si expone todas las resoluciones DASH y evita bloqueos 403.
YT_CLIENTS_VIDEO = ["android_creator", "default", "web_embedded", "tv", "ios", "android"]
_working_client = "default"
# None = todavia no se sabe si hacen falta cookies.
# []   = el analisis funciono SIN cookies: la descarga tampoco debe usarlas.
# Antes valia [] al empezar, y como una lista vacia cuenta como "falso", la
# descarga volvia a pedir las cookies del navegador aunque no hicieran falta; con
# Firefox abierto eso fallaba con "Permission denied ... cookies.sqlite".
_working_cookies = None
# Se activa si el navegador tiene bloqueada su base de cookies (navegador
# abierto). A partir de ahi se trabaja sin cookies durante toda la sesion.
_cookies_disabled = False


def client_arg(client):
    if client == "default":
        return []
    return ["--extractor-args", "youtube:player_client=%s" % client]


def cookies_arg():
    c = BASE_DIR / "cookies.txt"
    if c.exists():
        return ["--cookies", str(c)]
    return []


def _cookie_db_readable(path):
    """True si se puede leer la base de cookies en este momento.

    Con el navegador abierto, Windows la tiene bloqueada en exclusiva y yt-dlp
    falla al copiarla ("Permission denied ... cookies.sqlite" en Firefox,
    "Could not copy Chrome cookie database" en Chrome/Edge). Se comprueba antes
    de pasarsela a yt-dlp, para no provocar ese error.
    """
    try:
        with open(path, "rb") as f:
            f.read(16)
        return True
    except OSError:
        return False


def cookies_from_browser_arg():
    local = os.environ.get("LOCALAPPDATA", "")
    apdata = os.environ.get("APPDATA", "")

    firefox_profiles = os.path.join(apdata, "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(firefox_profiles):
        try:
            candidatos = []
            for entry in os.scandir(firefox_profiles):
                db = os.path.join(entry.path, "cookies.sqlite")
                if entry.is_dir() and os.path.exists(db):
                    candidatos.append((os.path.getmtime(db), entry.path, db))
            # El perfil usado mas recientemente primero, que es el que tiene la
            # sesion de YouTube al dia.
            for _, perfil, db in sorted(candidatos, reverse=True):
                if _cookie_db_readable(db):
                    # Se pasa la ruta exacta: con "firefox" a secas yt-dlp elige
                    # el perfil por su cuenta, que podria ser otro bloqueado.
                    return ["--cookies-from-browser", "firefox:%s" % perfil]
                log("Cookies de Firefox bloqueadas (navegador abierto): %s" % db)
        except Exception:
            pass

    # Solo la ruta clasica Default\Cookies, a proposito. Chrome/Edge/Brave
    # modernos guardan las cookies en Network\Cookies, pero desde la version 127
    # las cifran de una forma que yt-dlp no puede leer en Windows ("Failed to
    # decrypt with DPAPI", comprobado). Buscar esa ruta solo haria que cada
    # descarga probara primero unas cookies que siempre fallan.
    navegadores = [
        ("edge", os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cookies")),
        ("chrome", os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cookies")),
        ("brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cookies")),
        ("opera", os.path.join(apdata, "Opera Software", "Opera Stable", "Cookies")),
    ]
    for name, db in navegadores:
        if os.path.exists(db):
            if _cookie_db_readable(db):
                return ["--cookies-from-browser", name]
            log("Cookies de %s bloqueadas (navegador abierto): %s" % (name, db))
    return []


def all_cookies_arg():
    explicit = cookies_arg()
    if explicit:
        return explicit
    # Si ya sabemos que la base de cookies del navegador esta bloqueada (pasa
    # cuando Chrome/Edge estan abiertos: Windows no deja copiar el archivo),
    # no tiene sentido reintentar; solo genera errores feos y perdida de tiempo.
    if _cookies_disabled:
        return []
    return cookies_from_browser_arg()


# Navegador abierto: el archivo de cookies esta bloqueado. Se arregla cerrandolo.
_COOKIE_LOCK_MARKERS = (
    "cookie database",     # Chrome/Edge: "Could not copy Chrome cookie database"
    "cookies database",    # Firefox: "could not find firefox cookies database"
    "database is locked",
)
# Firefox abierto: "[Errno 13] Permission denied: '...\\cookies.sqlite'".
_COOKIE_LOCK_RE = re.compile(r"permission denied:\s*'[^']*cookies(?:\.sqlite)?'", re.IGNORECASE)

# Chrome/Edge 127+: cookies cifradas que yt-dlp no puede leer en Windows. Cerrar
# el navegador NO lo arregla, asi que necesita otro mensaje.
_COOKIE_CRYPT_MARKERS = (
    "failed to decrypt",
    "cannot decrypt",
    "unable to decrypt",
)


def cookie_error_kind(output):
    """Por que fallaron las cookies del navegador: 'bloqueo', 'cifrado' o None.

    OJO: no se busca "Extracting cookies from", porque yt-dlp escribe esa linea
    tambien cuando las cookies se leen bien; un fallo por otro motivo (un 403,
    por ejemplo) se confundiria con un problema de cookies y se desactivarian
    unas cookies que si funcionaban.
    """
    text = output or ""
    low = text.lower()
    if any(m in low for m in _COOKIE_CRYPT_MARKERS):
        return "cifrado"
    if any(m in low for m in _COOKIE_LOCK_MARKERS) or _COOKIE_LOCK_RE.search(text):
        return "bloqueo"
    if "could not copy" in low and "cookie" in low:
        return "bloqueo"
    return None


def is_cookie_error(output):
    """True si yt-dlp fallo por las cookies del navegador y no por YouTube.

    Antes solo reconocia el texto de Chrome, asi que con Firefox abierto el
    error ("Permission denied ... cookies.sqlite") pasaba sin detectar y se le
    mostraba crudo al usuario.
    """
    return cookie_error_kind(output) is not None


def cookie_error_message(output):
    """Mensaje para el usuario segun el tipo de fallo de cookies."""
    if cookie_error_kind(output) == "cifrado":
        return ("Tu navegador protege sus cookies y el programa no puede usarlas. "
                "Si YouTube pide verificacion, inicia sesion en YouTube con Firefox "
                "o pon un archivo cookies.txt junto al programa.")
    return ("No se pudieron usar las cookies de tu navegador porque esta abierto. "
            "Cierra Firefox, Chrome o Edge por completo y vuelve a intentarlo.")


def is_blocked(output):
    low = output.lower()
    return ("not a bot" in low or
            "sign in to confirm" in low or
            "confirm you're not a bot" in low or
            "403: forbidden" in low or
            "http error 403" in low or
            "unable to download video data" in low)


@app.route("/favicon.ico")
def favicon():
    return send_file(Path(base_dir) / "static" / "icon.ico", mimetype="image/x-icon")

@app.route("/api/check_update")
def api_check_update():
    update_info = check_for_updates()
    if update_info:
        return jsonify(update_info)
    return jsonify({"has_update": False, "current_version": APP_VERSION})


@app.route("/api/trigger_update", methods=["POST"])
def api_trigger_update():
    data = request.get_json() or {}
    download_url = data.get("download_url")
    if not download_url:
        return jsonify({"success": False, "error": "URL de descarga no valida."})

    def run_update():
        try:
            temp_installer = BASE_DIR / "update_setup.exe"
            log("Descargando actualizacion desde %s..." % download_url)
            r = requests.get(download_url, stream=True, timeout=300)
            r.raise_for_status()
            with open(temp_installer, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            log("Ejecutando instalador de actualizacion...")
            subprocess.Popen([str(temp_installer)], creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)
            time.sleep(2)
            os._exit(0)
        except Exception as e:
            log("Error en auto-update: %s" % e)
            socketio.emit("update_error", {"error": str(e)})

    threading.Thread(target=run_update, daemon=True).start()
    return jsonify({"success": True, "message": "Descargando e instalando actualizacion..."})


# ========================
#  MAIN APP
# ========================

KEY_FILE = BASE_DIR / ".bpmstart_key"


def _fallback_key_file():
    """Ubicacion alternativa dentro del perfil del usuario.

    Si el programa quedo en una carpeta sin permiso de escritura (portable
    descomprimido en Archivos de programa, unidad de red, o el antivirus
    bloqueando el archivo), guardar el codigo fallaba y la app decia
    "Error de conexion". Aqui siempre se puede escribir.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "BPMStartPro" / ".bpmstart_key"


def get_user_key():
    for ruta in (KEY_FILE, _fallback_key_file()):
        try:
            if ruta.exists():
                valor = ruta.read_text(encoding="utf-8").strip()
                if valor:
                    return valor
        except Exception:
            continue
    return None


def save_user_key(key):
    """Guarda el codigo. Devuelve la ruta usada o lanza excepcion si ninguna sirve."""
    key = key.strip()
    errores = []
    for ruta in (KEY_FILE, _fallback_key_file()):
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(key, encoding="utf-8")
            # Comprobar que de verdad quedo escrito: algunos antivirus dejan
            # pasar la escritura y luego borran el archivo.
            if ruta.read_text(encoding="utf-8").strip() == key:
                return ruta
            errores.append("%s: no se pudo verificar" % ruta)
        except Exception as e:
            errores.append("%s: %s" % (ruta, e))
    raise IOError("; ".join(errores))


def delete_user_key():
    for ruta in (KEY_FILE, _fallback_key_file()):
        try:
            if ruta.exists():
                ruta.unlink()
        except Exception:
            pass


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    key = get_user_key()
    return jsonify({"has_key": bool(key), "key": ("*" * len(key) if key else "")})


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    data = request.get_json()
    codigo = data.get("codigo", "").strip()
    if not codigo:
        return jsonify({"success": False, "error": "El codigo no puede estar vacio"})
    if len(codigo) < 20:
        return jsonify({"success": False, "error": "El codigo parece demasiado corto"})
    try:
        save_user_key(codigo)
    except Exception as e:
        # Antes esto reventaba con un 500 en HTML y la interfaz lo mostraba
        # como "Error de conexion", que despistaba por completo.
        log("No se pudo guardar el codigo: %s" % e)
        return jsonify({"success": False, "error": (
            "No se pudo guardar el codigo en el disco. Revisa que el antivirus no "
            "este bloqueando el programa, o instalalo en otra carpeta.")})
    return jsonify({"success": True})


@app.route("/api/settings/delete", methods=["POST"])
def api_settings_delete():
    delete_user_key()
    return jsonify({"success": True})

CATEGORIES = {
    "vocal": {
        "name": "Vocal", "icon": "🎤", "short_desc": "6 modelos",
        "sub": {
            "general":     {"name": "General (BS Roformer)",          "sep_type": 40,  "add_opt1": "81",  "add_opt2": "2",   "add_opt3": None, "labels": ["Vocales", "Instrumental"]},
            "polarformer": {"name": "BS PolarFormer",                 "sep_type": 123, "add_opt1": "163", "add_opt2": "2",   "add_opt3": None, "labels": ["Vocales", "Instrumental"]},
            "mdx23c":      {"name": "MDX23C",                         "sep_type": 25,  "add_opt1": "7",   "add_opt2": None,  "add_opt3": None, "labels": ["Vocales", "Instrumental"]},
            "scnet":       {"name": "SCNet XL",                       "sep_type": 46,  "add_opt1": "5",   "add_opt2": None,  "add_opt3": None, "labels": ["Vocales", "Instrumental"]},
            "melband":     {"name": "MelBand Roformer",               "sep_type": 48,  "add_opt1": "4",   "add_opt2": "2",   "add_opt3": None, "labels": ["Vocales", "Instrumental"]},
            "demucs4ht":   {"name": "Demucs4 HT (4 pistas)",          "sep_type": 20,  "add_opt1": "0",   "add_opt2": None,  "add_opt3": None, "labels": ["Vocales", "Bajo", "Bateria", "Otro"]},
        },
    },
    "vocal_7": {
        "name": "Vocal 7", "icon": "🎵", "short": "7 pistas",
        "sep_type": 63, "add_opt1": None, "add_opt2": None, "add_opt3": None,
        "labels": ["Vocales", "Bajo", "Bateria", "Guitarra", "Piano", "Otro", "Instrumental"],
    },
    "drums": {
        "name": "Bateria", "icon": "🥁", "short": "1 modelo",
        "sep_type": 44, "add_opt1": "6", "add_opt2": "0", "add_opt3": "0",
        "labels": ["Bateria", "Otro"],
    },
    "bass": {
        "name": "Bajo", "icon": "🎸", "short": "1 modelo",
        "sep_type": 41, "add_opt1": "5", "add_opt2": "0", "add_opt3": "0",
        "labels": ["Bajo", "Otro"],
    },
    "synth": {
        "name": "Sintetizador", "icon": "🔊", "short": "1 modelo",
        "sep_type": 88, "add_opt1": "0", "add_opt2": None, "add_opt3": None,
        "labels": ["Sintetizador", "Otro"],
    },
    "keys": {
        "name": "Teclados", "icon": "🎹", "short": "9 modelos",
        "sub": {
            "piano":         {"name": "Piano",               "sep_type": 29,  "add_opt1": "5",  "add_opt2": None, "labels": ["Piano", "Otro"]},
            "piano_digital": {"name": "Piano Digital",       "sep_type": 79,  "add_opt1": None, "add_opt2": "0",  "labels": ["Piano Digital", "Otro"]},
            "organo":        {"name": "Organo",              "sep_type": 58,  "add_opt1": "3",  "add_opt2": None, "labels": ["Organo", "Otro"]},
            "teclados":      {"name": "Teclados",            "sep_type": 106, "add_opt1": None, "add_opt2": None, "labels": ["Teclados", "Otro"]},
            "clavicordio":   {"name": "Clavicordio",         "sep_type": 91,  "add_opt1": None, "add_opt2": None, "labels": ["Clavicordio", "Otro"]},
            "acordeon":      {"name": "Acordeon",            "sep_type": 99,  "add_opt1": None, "add_opt2": None, "labels": ["Acordeon", "Otro"]},
            "vibrafono":     {"name": "Vibrafono",           "sep_type": 129, "add_opt1": None, "add_opt2": None, "labels": ["Vibrafono", "Otro"]},
            "rhodes":        {"name": "Rhodes",              "sep_type": 131, "add_opt1": None, "add_opt2": None, "labels": ["Rhodes", "Otro"]},
            "metal_bars":    {"name": "Campanas Metalicas",   "sep_type": 130, "add_opt1": None, "add_opt2": None, "labels": ["Campanas Metalicas", "Otro"]},
        },
    },
    "guitar": {
        "name": "Guitarras", "icon": "🎸", "short": "5 modelos",
        "sub": {
            "guitar_general":    {"name": "Guitarra General",       "sep_type": 31,  "add_opt1": "7",  "add_opt2": None, "labels": ["Guitarra", "Otro"]},
            "acoustic_guitar":   {"name": "Guitarra Acustica",      "sep_type": 66,  "add_opt1": None, "add_opt2": "0",  "labels": ["Guitarra Acustica", "Otro"]},
            "electric_guitar":   {"name": "Guitarra Electrica",     "sep_type": 81,  "add_opt1": None, "add_opt2": "0",  "labels": ["Guitarra Electrica", "Otro"]},
            "lead_rhythm":       {"name": "Lead / Ritmica",         "sep_type": 101, "add_opt1": "0",  "add_opt2": None, "labels": ["Lead", "Ritmica"]},
            "pedal_steel":      {"name": "Pedal Steel Guitar",     "sep_type": 124, "add_opt1": None, "add_opt2": None, "labels": ["Pedal Steel", "Otro"]},
        },
    },
    "wind": {
        "name": "Vientos", "icon": "🎺", "short": "15 modelos",
        "sub": {
            "wind_general":  {"name": "Vientos General",     "sep_type": 54,  "add_opt1": "3",  "add_opt2": "0", "labels": ["Vientos", "Otro"]},
            "brass":         {"name": "Latones",              "sep_type": 107, "add_opt1": "0",  "add_opt2": None, "labels": ["Latones", "Otro"]},
            "woodwind":      {"name": "Maderas",              "sep_type": 108, "add_opt1": "0",  "add_opt2": None, "labels": ["Maderas", "Otro"]},
            "saxophone":     {"name": "Saxofon",              "sep_type": 61,  "add_opt1": "3",  "add_opt2": None, "labels": ["Saxofon", "Otro"]},
            "flute":         {"name": "Flauta",               "sep_type": 67,  "add_opt1": "1",  "add_opt2": "0",  "labels": ["Flauta", "Otro"]},
            "trumpet":       {"name": "Trompeta",             "sep_type": 71,  "add_opt1": None, "add_opt2": "0",  "labels": ["Trompeta", "Otro"]},
            "trombone":      {"name": "Trombon",              "sep_type": 75,  "add_opt1": None, "add_opt2": "0",  "labels": ["Trombon", "Otro"]},
            "oboe":          {"name": "Oboe",                  "sep_type": 77,  "add_opt1": None, "add_opt2": "0",  "labels": ["Oboe", "Otro"]},
            "clarinet":      {"name": "Clarinete",            "sep_type": 78,  "add_opt1": None, "add_opt2": "0",  "labels": ["Clarinete", "Otro"]},
            "french_horn":   {"name": "Corno Frances",        "sep_type": 82,  "add_opt1": None, "add_opt2": "0",  "labels": ["Corno Frances", "Otro"]},
            "harmonica":     {"name": "Armonica",             "sep_type": 87,  "add_opt1": None, "add_opt2": "0",  "labels": ["Armonica", "Otro"]},
            "tuba":          {"name": "Tuba",                  "sep_type": 92,  "add_opt1": None, "add_opt2": None, "labels": ["Tuba", "Otro"]},
            "bassoon":       {"name": "Fagot",                 "sep_type": 93,  "add_opt1": None, "add_opt2": None, "labels": ["Fagot", "Otro"]},
            "bagpipes":      {"name": "Gaita",                 "sep_type": 116, "add_opt1": None, "add_opt2": "0",  "labels": ["Gaita", "Otro"]},
            "whistle":       {"name": "Silbato",               "sep_type": 132, "add_opt1": None, "add_opt2": None, "labels": ["Silbato", "Otro"]},
        },
    },
    "strings": {
        "name": "Cuerdas", "icon": "🎻", "short": "12 modelos",
        "sub": {
            "bowed":         {"name": "Cuerdas Frotadas",       "sep_type": 52,  "add_opt1": "1",  "add_opt2": "0",  "labels": ["Cuerdas", "Otro"]},
            "plucked":       {"name": "Cuerdas Pulsadas",       "sep_type": 102, "add_opt1": None, "add_opt2": None, "labels": ["Cuerdas Pulsadas", "Otro"]},
            "violin":        {"name": "Violin",                  "sep_type": 65,  "add_opt1": None, "add_opt2": None, "labels": ["Violin", "Otro"]},
            "viola":         {"name": "Viola",                    "sep_type": 69,  "add_opt1": None, "add_opt2": "0",   "labels": ["Viola", "Otro"]},
            "cello":         {"name": "Violonchelo",              "sep_type": 70,  "add_opt1": None, "add_opt2": "0",   "labels": ["Violonchelo", "Otro"]},
            "double_bass":   {"name": "Contrabajo",               "sep_type": 73,  "add_opt1": None, "add_opt2": "0",   "labels": ["Contrabajo", "Otro"]},
            "harp":          {"name": "Arpa",                     "sep_type": 72,  "add_opt1": None, "add_opt2": None, "labels": ["Arpa", "Otro"]},
            "mandolin":      {"name": "Mandolina",                "sep_type": 74,  "add_opt1": None, "add_opt2": None, "labels": ["Mandolina", "Otro"]},
            "banjo":         {"name": "Banjo",                    "sep_type": 83,  "add_opt1": None, "add_opt2": None, "labels": ["Banjo", "Otro"]},
            "sitar":         {"name": "Sitar",                    "sep_type": 90,  "add_opt1": None, "add_opt2": None, "labels": ["Sitar", "Otro"]},
            "ukulele":       {"name": "Ukelele",                  "sep_type": 96,  "add_opt1": None, "add_opt2": None, "labels": ["Ukelele", "Otro"]},
            "dobro":         {"name": "Dobro",                    "sep_type": 97,  "add_opt1": None, "add_opt2": None, "labels": ["Dobro", "Otro"]},
        },
    },
    "percussion": {
        "name": "Percusion", "icon": "🎶", "short": "11 modelos",
        "sub": {
            "perc_general":     {"name": "Percusion General",   "sep_type": 105, "add_opt1": None, "add_opt2": None, "labels": ["Percusion", "Otro"]},
            "tambourine":       {"name": "Pandereta",            "sep_type": 76,  "add_opt1": None, "add_opt2": None, "labels": ["Pandereta", "Otro"]},
            "marimba":          {"name": "Marimba",              "sep_type": 84,  "add_opt1": None, "add_opt2": None, "labels": ["Marimba", "Otro"]},
            "glockenspiel":     {"name": "Glockenspiel",         "sep_type": 85,  "add_opt1": None, "add_opt2": None, "labels": ["Glockenspiel", "Otro"]},
            "timpani":          {"name": "Timpani",               "sep_type": 86,  "add_opt1": None, "add_opt2": None, "labels": ["Timpani", "Otro"]},
            "triangle":         {"name": "Triangulo",             "sep_type": 89,  "add_opt1": None, "add_opt2": None, "labels": ["Triangulo", "Otro"]},
            "congas":           {"name": "Congas",                "sep_type": 94,  "add_opt1": None, "add_opt2": None, "labels": ["Congas", "Otro"]},
            "bells":            {"name": "Campanas",              "sep_type": 95,  "add_opt1": None, "add_opt2": None, "labels": ["Campanas", "Otro"]},
            "xylophone":        {"name": "Xilofono",              "sep_type": 109, "add_opt1": None, "add_opt2": "0",  "labels": ["Xilofono", "Otro"]},
            "celesta":          {"name": "Celesta",               "sep_type": 110, "add_opt1": None, "add_opt2": "0",  "labels": ["Celesta", "Otro"]},
            "cowbell":          {"name": "Cencerro",              "sep_type": 128, "add_opt1": None, "add_opt2": None, "labels": ["Cencerro", "Otro"]},
        },
    },
    "drumsep": {
        "name": "DrumSep", "icon": "🥁", "short": "6 pistas",
        "sep_type": 37, "add_opt1": "7", "add_opt2": "0", "add_opt3": None,
        "labels": ["Kick", "Snare", "HiHat", "Ride", "Crash", "Toms"],
    },
    "choir": {
        "name": "Coro/Voz", "icon": "👥", "short": "4 modelos",
        "sub": {
            "choir":          {"name": "Coro",                        "sep_type": 112, "add_opt1": None, "add_opt2": "0",  "labels": ["Coro", "Otro"]},
            "satb":           {"name": "SATB (Soprano/Alto/Tenor/Bajo)", "sep_type": 111, "add_opt1": "3",  "add_opt2": "0",  "labels": ["Soprano", "Alto", "Tenor", "Bajo"]},
            "male_female":    {"name": "Voz Masculina/Femenina",      "sep_type": 57,  "add_opt1": "2",  "add_opt2": "0",  "labels": ["Voz Masculina", "Voz Femenina"]},
            "medley_vox":     {"name": "Multi-cantante",              "sep_type": 53,  "add_opt1": "1",  "add_opt2": None, "labels": ["Vocales"]},
        },
    },
    "karaoke": {
        "name": "Karaoke", "icon": "🔇", "short": "2 modelos",
        "sub": {
            "karaoke":         {"name": "Karaoke (Lead/Coros)", "sep_type": 49,  "add_opt1": "6",  "add_opt2": "0",  "labels": ["Vocales Lead", "Coros"]},
            "mdxb_karaoke":    {"name": "MDX-B Karaoke",         "sep_type": 12,  "add_opt1": "0",  "add_opt2": None, "labels": ["Vocales Lead", "Coros"]},
        },
    },
    "effects": {
        "name": "Efectos", "icon": "✨", "short": "7 modelos",
        "sub": {
            "fx":            {"name": "Efectos FX",           "sep_type": 122, "add_opt1": None, "add_opt2": None, "labels": None},
            "reverb":        {"name": "Eliminar Reverb",      "sep_type": 22,  "add_opt1": "7",  "add_opt2": "1",  "labels": None},
            "denoise":       {"name": "Reducir Ruido",        "sep_type": 47,  "add_opt1": "0",  "add_opt2": None, "labels": None},
            "crowd":         {"name": "Eliminar Publico",     "sep_type": 34,  "add_opt1": "2",  "add_opt2": None, "labels": ["Voz", "Otro"]},
            "phantom":       {"name": "Centro Fantasma",      "sep_type": 55,  "add_opt1": "1",  "add_opt2": None, "labels": None},
            "braam":         {"name": "Braam (Cinematico)",   "sep_type": 117, "add_opt1": None, "add_opt2": None, "labels": None},
            "risers":        {"name": "Risers (Transiciones)", "sep_type": 125, "add_opt1": None, "add_opt2": None, "labels": None},
        },
    },
    "upscale": {
        "name": "Escalado", "icon": "⬆", "short": "4 modelos",
        "sub": {
            "audiosr":    {"name": "AudioSR",           "sep_type": 59, "add_opt1": "0",  "add_opt2": None, "labels": None},
            "flashsr":    {"name": "FlashSR",           "sep_type": 60, "add_opt1": None, "add_opt2": None, "labels": None},
            "apollo":     {"name": "Apollo Enhancers",  "sep_type": 51, "add_opt1": "3",  "add_opt2": "0",  "labels": None},
            "matchering": {"name": "Masterizacion",     "sep_type": 68, "add_opt1": None, "add_opt2": None, "labels": None},
        },
    },
    "voice_ai": {
        "name": "Voz IA", "icon": "🤖", "short": "3 modelos",
        "sub": {
            "vibe_clone":  {"name": "Clonar Voz",           "sep_type": 103, "add_opt1": "1",  "add_opt2": None, "labels": None},
            "vibe_tts":    {"name": "Texto a Voz",          "sep_type": 104, "add_opt1": "1",  "add_opt2": None, "labels": None},
            "qwen_clone":  {"name": "Clonar Voz Qwen3",    "sep_type": 120, "add_opt1": None, "add_opt2": None, "labels": None},
        },
    },
    "midi": {
        "name": "MIDI", "icon": "🎼", "short": "4 modelos",
        "sub": {
            "transkun":     {"name": "Piano a MIDI",           "sep_type": 113, "add_opt1": "0",  "add_opt2": None, "labels": None},
            "basic_pitch":  {"name": "Notas a MIDI",           "sep_type": 114, "add_opt1": None, "add_opt2": None, "labels": None},
            "some":         {"name": "Canto a MIDI",           "sep_type": 80,  "add_opt1": "1",  "add_opt2": None, "labels": None},
            "adtof":        {"name": "Bateria a MIDI",         "sep_type": 127, "add_opt1": "1",  "add_opt2": None, "labels": None},
        },
    },
    "generate": {
        "name": "Generar", "icon": "💿", "short": "2 modelos",
        "sub": {
            "heartmula":       {"name": "HeartMuLa (Cancion)",  "sep_type": 121, "add_opt1": None, "add_opt2": None, "labels": None},
            "stable_audio":    {"name": "Stable Audio Open",    "sep_type": 62,  "add_opt1": None, "add_opt2": None, "labels": None},
        },
    },
}


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip(". ")
    return name[:200] if name else "video"


def parse_progress(line):
    patterns = {
        "percent": r"(\d+\.?\d*)%",
        "speed": r"at\s+([\d.]+\w+/s)",
        "eta": r"ETA\s+(\d+:\d+(?::\d+)?)",
        "size": r"of\s+~?([\d.]+\w+)",
    }
    result = {}
    for key, pat in patterns.items():
        m = re.search(pat, line)
        if m:
            result[key] = m.group(1)
    if "[download]" in line and "Destination" in line:
        m = re.search(r"Destination:\s+(.+)", line)
        if m:
            result["filename"] = os.path.basename(m.group(1).strip())
    if "[download]" in line and "already downloaded" in line:
        result["already"] = True
    return result


def get_video_resolution(file_path):
    """(ancho, alto) real del video en pixeles, o (0, 0) si no se pudo determinar."""
    ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
    try:
        res = subprocess.run(
            [str(ffmpeg), "-i", str(file_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        match = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", res.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception as e:
        log(f"Error verificando resolucion: {e}")
    return 0, 0


def get_video_height(file_path):
    """Altura real del video en pixeles, o 0 si no se pudo determinar."""
    return get_video_resolution(file_path)[1]


def get_video_quality_tier(w, h):
    """Determina la etiqueta de calidad (2K, 1080p, 720p, etc.) para cualquier aspecto (16:9, ultrawide 21:9, vertical 9:16, 4:3)."""
    long_edge = max(w, h)
    short_edge = min(w, h)
    if long_edge >= 2400 or short_edge >= 1400:
        return "2K"
    if long_edge >= 1800 or short_edge >= 1000:
        return "1080p"
    if long_edge >= 1200 or short_edge >= 700:
        return "720p"
    if long_edge >= 800 or short_edge >= 460:
        return "480p"
    if long_edge >= 600 or short_edge >= 340:
        return "360p"
    if short_edge > 0:
        return f"{short_edge}p"
    return "Desconocida"


def is_tier_satisfied(target_tag, w, h):
    """Retorna True si las dimensiones reales satisfacen o superan la calidad solicitada."""
    long_edge = max(w, h)
    short_edge = min(w, h)
    if target_tag == "2K":
        return long_edge >= 2400 or short_edge >= 1400
    if target_tag == "1080p":
        return long_edge >= 1800 or short_edge >= 1000
    if target_tag == "720p":
        return long_edge >= 1200 or short_edge >= 700
    return True


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("analyze")
def handle_analyze(data):
    url = data.get("url", "").strip()
    if not url:
        emit("analyze_error", {"error": "Por favor ingresa una URL valida."})
        return

    emit("analyzing", {"status": "Obteniendo informacion del video..."})

    try:
        global _working_client, _working_cookies, _cookies_disabled
        ytdlp_path = get_ytdlp_path()
        js_runtime = get_js_runtime_arg()
        info = None
        last_err = ""
        cookies_bloqueadas = ""  # texto del error de cookies, si lo hubo

        strategies = [([], "sin cookies")]
        all_cookies = all_cookies_arg()
        if all_cookies:
            strategies.append((all_cookies, "con cookies"))
        for cookie_args, tag in strategies:
            for client in YT_CLIENTS:
                cmd = [ytdlp_path, "--no-download", "--print-json", "--no-playlist"] + js_runtime + cookie_args + client_arg(client) + [url]
                log("Analizando %s | js-runtime: %s | client: %s | %s (%s)" % (
                    url, " | ".join(js_runtime) if js_runtime else "NO ENCONTRADO", client, tag,
                    " | ".join(cookie_args) if cookie_args else "ninguna"))
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                except subprocess.TimeoutExpired:
                    emit("analyze_error", {"error": "Tiempo de espera agotado. Verifica la URL."})
                    return

                if result.returncode == 0:
                    info = json.loads(result.stdout)
                    _working_client = client
                    _working_cookies = cookie_args
                    break
                # El ERROR de yt-dlp va al final: con [:200] se cortaba justo esa parte.
                stderr_txt = result.stderr.strip()
                log("Client %s %s fallo: %s" % (client, tag, stderr_txt[-300:]))

                # Las cookies del navegador no se pueden leer (navegador abierto o
                # cifradas). Reintentar con cada cliente solo repite el mismo
                # error, asi que se abandona esta estrategia de una vez. Y NO se
                # guarda como last_err: taparia el motivo real por el que fallo
                # el intento sin cookies.
                if cookie_args and is_cookie_error(stderr_txt):
                    _cookies_disabled = True
                    _working_cookies = []
                    cookies_bloqueadas = stderr_txt
                    log("Cookies del navegador desactivadas: no se pueden leer (%s)" % cookie_error_kind(stderr_txt))
                    break

                last_err = stderr_txt[-300:]

            if info:
                break

        if not info:
            if cookies_bloqueadas:
                # Mensaje entendible en vez del error crudo de yt-dlp con rutas y
                # enlaces a GitHub, que solo alarma al usuario.
                emit("analyze_error", {"error": "YouTube pidio verificacion. " + cookie_error_message(cookies_bloqueadas)})
                return
            msg = "No se pudo obtener info: %s" % last_err
            if is_blocked(last_err):
                msg += (" | YouTube bloqueo la extraccion desde esta red/navegador. "
                        "Solucion: 1) Instala Firefox, inicia sesion en youtube.com con tu cuenta de Google y vuelve a abrir el programa. "
                        "2) O exporta tus cookies con una extension 'Get cookies.txt LOCALLY' y pon cookies.txt junto al programa. "
                        "3) O prueba con otra conexion.")
            emit("analyze_error", {"error": msg})
            return

        title = info.get("title", "Sin titulo")
        thumbnail = info.get("thumbnail", "")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "Desconocido")

        mins = duration // 60
        secs = duration % 60

        emit("video_info", {
            "title": title,
            "thumbnail": thumbnail,
            "uploader": uploader,
            "duration": f"{mins}:{secs:02d}",
            "id": info.get("id", ""),
        })
    except subprocess.TimeoutExpired:
        emit("analyze_error", {"error": "Tiempo de espera agotado. Verifica la URL."})
    except json.JSONDecodeError:
        emit("analyze_error", {"error": "Error al procesar la informacion del video."})
    except Exception as e:
        emit("analyze_error", {"error": f"Error inesperado: {str(e)[:200]}"})


@socketio.on("start_download")
def handle_download(data):
    url = data.get("url", "").strip()
    fmt = data.get("format", "mp3")
    quality = data.get("quality", "best")
    title = data.get("title", "video")

    if not url:
        emit("download_error", {"error": "URL no valida."})
        return

    download_id = str(uuid.uuid4())[:8]
    safe_title = sanitize_filename(title)

    if fmt == "mp4":
        tag = "2K" if quality == "best" else "1080p" if quality == "medium" else "720p"
        max_dim = 2560 if quality == "best" else 1920 if quality == "medium" else 1280
    else:
        tag = "320k" if quality == "best" else "192k" if quality == "medium" else "128k"
        max_dim = 0

    output_filename_base = f"{safe_title} [{tag}]"
    output_template = str(DOWNLOADS_DIR / f"{output_filename_base}.%(ext)s")

    ytdlp_path = get_ytdlp_path()
    ffmpeg_dir = get_ffmpeg_dir()
    # "is not None" y no un simple "if": una lista vacia significa que el
    # analisis YA confirmo que no hacen falta cookies. Con el "if" de antes
    # contaba como falso y se volvian a pedir las del navegador, que con
    # Firefox abierto fallaban ("Permission denied ... cookies.sqlite").
    initial_cookies = _working_cookies if _working_cookies is not None else all_cookies_arg()
    # Las cookies van aparte del comando base para poder quitarlas en caliente
    # si el navegador las tiene bloqueadas.
    cmd_base = [ytdlp_path, "--no-playlist", "--force-overwrites", "-o", output_template, "--ffmpeg-location", ffmpeg_dir] + get_js_runtime_arg()

    if fmt == "mp3":
        cmd_base += ["-x", "--audio-format", "mp3", "--audio-quality",
                 "320K" if quality == "best" else "192K" if quality == "medium" else "128K"]
    elif fmt == "mp4":
        # Bounding box [width<=max_dim][height<=max_dim] garantiza que tanto videos 16:9
        # como ultrawide (21:9 o 2.40:1) y verticales (Shorts 9:16) no excedan el escalon pedido.
        cmd_base += ["-f", "bv*[width<=%d][height<=%d]+ba[ext=m4a]/bv*[width<=%d][height<=%d]+ba/b[width<=%d][height<=%d]" % (
            max_dim, max_dim, max_dim, max_dim, max_dim, max_dim)]
        # Orden de preferencia: 1) la resolucion mas alta permitida, 2) a igual
        # resolucion, h264 (avc1) antes que AV1/VP9 porque es mas compatible,
        # 3) el bitrate mas alto, que es lo que evita las copias AV1 de bitrate
        # bajo que se veian borrosas. Por encima de 1080p YouTube no ofrece
        # avc1, asi que ahi entra vp9/av1 automaticamente.
        cmd_base += ["-S", "res,vcodec:avc1,br"]
        cmd_base += ["--merge-output-format", "mp4"]

    active_downloads[download_id] = {"file": None, "title": output_filename_base}

    emit("download_started", {"download_id": download_id})

    def run_download():
        try:
            global _working_client, _cookies_disabled
            base_clients = YT_CLIENTS_VIDEO if fmt == "mp4" else YT_CLIENTS
            # "android" suele funcionar para /api/analyze (metadatos) y puede
            # haber quedado cacheado como _working_client, pero para video no
            # nos sirve como primera opcion (ver YT_CLIENTS_VIDEO). Ignoramos
            # el cache en ese caso puntual.
            preferred = None if (fmt == "mp4" and _working_client == "android") else _working_client
            clients = [preferred] + [c for c in base_clients if c != preferred] if preferred in base_clients else list(base_clients)
            last_err = "Error durante la descarga. Verifica la URL."

            target_tag = tag if fmt == "mp4" else ""
            # Respaldo por si NINGUN cliente consigue la calidad pedida: en vez de
            # descartar la unica descarga que si funciono, la apartamos y la
            # restauramos al final avisando al usuario. Va en un subdirectorio
            # para que no la reencuentren los fallbacks de deteccion de archivo.
            lowq_dir = DOWNLOADS_DIR / ".tmp_lowq"
            lowq_backup = None

            cookie_args = list(initial_cookies)
            cookies_fallaron = False
            # Bucle con indice (y no un for) para poder repetir el mismo cliente
            # sin cookies si fallan. Cada "continue" sigue pasando al siguiente
            # cliente, porque el indice ya avanzo arriba.
            idx = 0
            while idx < len(clients):
                client_idx = idx
                client = clients[idx]
                idx += 1
                cmd = cmd_base + cookie_args + client_arg(client) + [url]
                log("Descargando %s | fmt: %s | quality: %s (%s) | client: %s | cookies: %s" % (
                    url, fmt, quality, tag, client, "si" if cookie_args else "no"))
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW
                )

                output_lines = []
                last_percent = -1
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    output_lines.append(line)
                    progress = parse_progress(line)
                    if "filename" in progress:
                        active_downloads[download_id]["file"] = progress["filename"]
                    if "already" in progress:
                        socketio.emit("progress", {
                            "download_id": download_id,
                            "percent": 100,
                            "speed": "-",
                            "eta": "00:00",
                            "status": "Archivo ya descargado",
                        })
                    elif "percent" in progress:
                        pct = float(progress["percent"])
                        if pct != last_percent:
                            last_percent = pct
                            socketio.emit("progress", {
                                "download_id": download_id,
                                "percent": pct,
                                "speed": progress.get("speed", "-"),
                                "eta": progress.get("eta", "--:--"),
                                "status": f"Descargando... {pct}%",
                            })

                process.wait()

                if process.returncode == 0:
                    downloaded_file = None
                    target_ext = f".{fmt.lower()}"
                    
                    # 1. First look for exact match with output_filename_base
                    for f in DOWNLOADS_DIR.iterdir():
                        if f.is_file() and f.stem == output_filename_base and f.suffix.lower() == target_ext:
                            downloaded_file = f.name
                            break

                    # 2. Fallback: match output_filename_base with any extension
                    if not downloaded_file:
                        for f in DOWNLOADS_DIR.iterdir():
                            if f.is_file() and f.stem == output_filename_base:
                                downloaded_file = f.name
                                break

                    # 3. Fallback: match safe_title without tag
                    if not downloaded_file:
                        for f in DOWNLOADS_DIR.iterdir():
                            if f.is_file() and f.stem == safe_title and f.suffix.lower() == target_ext:
                                downloaded_file = f.name
                                break

                    # 4. Fallback: get most recently modified file in DOWNLOADS_DIR
                    if not downloaded_file:
                        files = [f for f in DOWNLOADS_DIR.iterdir() if f.is_file()]
                        if files:
                            files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                            downloaded_file = files[0].name

                    if downloaded_file:
                        actual_w, actual_h = 0, 0
                        if fmt == "mp4":
                            src = DOWNLOADS_DIR / downloaded_file
                            actual_w, actual_h = get_video_resolution(src)
                            log("Video descargado resolucion real: %sx%s (solicitado: %s)" % (actual_w, actual_h, target_tag))
                            if target_tag in ("2K", "1080p", "720p") and actual_w > 0 and (actual_w <= 640 and actual_h <= 360):
                                log("Descarga client %s dio solo %sx%s (se pidio %s), probando otro cliente" % (
                                    client, actual_w, actual_h, target_tag))
                                try:
                                    actual_area = actual_w * actual_h
                                    if lowq_backup is None or actual_area > (lowq_backup[1] * lowq_backup[2]):
                                        lowq_dir.mkdir(exist_ok=True)
                                        backup = lowq_dir / downloaded_file
                                        if backup.exists():
                                            backup.unlink()
                                        src.rename(backup)
                                        lowq_backup = (backup, actual_w, actual_h, downloaded_file)
                                    else:
                                        src.unlink()
                                except Exception:
                                    try:
                                        src.unlink()
                                    except Exception:
                                        pass
                                last_err = "Solo se consiguio %sx%s, probando otro cliente..." % (actual_w, actual_h)
                                continue

                        # Descarga buena: ya no hace falta el respaldo de baja calidad.
                        _discard_lowq(lowq_backup, lowq_dir)

                        ext = Path(downloaded_file).suffix.lstrip(".")
                        msg_warning = None
                        if fmt == "mp4" and actual_w and actual_h and not is_tier_satisfied(target_tag, actual_w, actual_h):
                            actual_tier = get_video_quality_tier(actual_w, actual_h)
                            msg_warning = (
                                "El video se descargo en %s (maxima resolucion disponible en YouTube para este video)."
                                % actual_tier
                            )

                        socketio.emit("download_complete", {
                            "download_id": download_id,
                            "filename": downloaded_file,
                            "title": output_filename_base,
                            "ext": ext,
                            "quality_warning": msg_warning,
                        })
                    else:
                        socketio.emit("download_error", {
                            "download_id": download_id,
                            "error": "Archivo no encontrado tras la descarga.",
                        })
                    return

                full_out = "\n".join(output_lines[-40:])
                log("Descarga client %s fallo: %s" % (client, full_out[-200:]))

                # Fallo por las cookies del navegador (Firefox/Chrome abierto), no
                # por YouTube. Se desactivan y se repite ESTE MISMO cliente sin
                # ellas: pasar al siguiente seria perder justo el cliente que el
                # analisis ya comprobo que funciona. Solo puede ocurrir una vez,
                # porque despues cookie_args queda vacio.
                if cookie_args and is_cookie_error(full_out):
                    _cookies_disabled = True
                    cookie_args = []
                    cookies_fallaron = True
                    log("Cookies del navegador no legibles; se reintenta el client %s sin ellas" % client)
                    socketio.emit("progress", {
                        "download_id": download_id,
                        "percent": 0,
                        "speed": "-",
                        "eta": "--:--",
                        "status": "Reintentando sin las cookies del navegador...",
                    })
                    idx -= 1
                    continue

                last_err = full_out if full_out.strip() else last_err

            # Ningun cliente logro la resolucion pedida. Si al menos uno bajo algo
            # (aunque fuera 360p), es mejor entregarlo avisando que fallar del todo.
            if lowq_backup:
                backup, actual_w, actual_h, orig_name = lowq_backup
                final = DOWNLOADS_DIR / orig_name
                try:
                    if final.exists():
                        final.unlink()
                    backup.rename(final)
                    try:
                        lowq_dir.rmdir()
                    except Exception:
                        pass
                    actual_tier = get_video_quality_tier(actual_w, actual_h)
                    log("Ningun cliente supero %s; se entrega la mejor descarga disponible" % actual_tier)
                    socketio.emit("download_complete", {
                        "download_id": download_id,
                        "filename": orig_name,
                        "title": output_filename_base,
                        "ext": Path(orig_name).suffix.lstrip("."),
                        "quality_warning": (
                            "YouTube solo permitio %s para este video en esta red. "
                            "Intenta de nuevo mas tarde o usa cookies para obtener %s."
                            % (actual_tier, target_tag)
                        ),
                    })
                    return
                except Exception as e:
                    log("No se pudo restaurar la descarga de respaldo: %s" % e)

            if is_cookie_error(last_err):
                # Nunca mostrar el error crudo ("Extracting cookies from firefox
                # ERROR: [Errno 13] Permission denied: 'C:\\Users\\...'"), que
                # no le dice nada util al usuario.
                err_msg = cookie_error_message(last_err)
            else:
                err_msg = last_err[-200:] if last_err else "Error durante la descarga. Verifica la URL."
                if is_blocked(last_err):
                    err_msg += " (YouTube esta limitando descargas directas. Intenta con cookies o reinicia la aplicacion)."
                    if cookies_fallaron:
                        err_msg += (" Si tienes Firefox, Chrome o Edge abierto, cierralo: el programa "
                                    "no pudo usar sus cookies para superar la verificacion.")

            socketio.emit("download_error", {
                "download_id": download_id,
                "error": err_msg,
            })
        except Exception as e:
            socketio.emit("download_error", {
                "download_id": download_id,
                "error": f"Error: {str(e)[:200]}",
            })

    thread = threading.Thread(target=run_download, daemon=True)
    thread.start()


@app.route("/download_file/<path:filename>")
def download_file(filename):
    file_path = DOWNLOADS_DIR / filename
    if file_path.exists():
        ext = file_path.suffix.lower()
        mime = MIME_MAP.get(ext, "application/octet-stream")
        return send_file(file_path, as_attachment=True, mimetype=mime)
    return jsonify({"error": "Archivo no encontrado"}), 404


def _cleanup(path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


# ========================
#  SEPARACION (BPMStartPRO)
# ========================

def _build_multipart_stream(fields, file_field, file_path, on_progress=None, chunk_size=262144):
    """Arma el cuerpo multipart como flujo, para poder informar el avance.

    Pasarle el archivo a requests con files={...} hace que lo lea entero de una
    sola vez en memoria: no hay avance que mostrar y la barra se queda clavada
    en 7% durante toda la subida. Enviandolo por trozos si sabemos cuanto va.

    Se calcula Content-Length a mano y no se usa "chunked", porque no todos los
    servidores lo aceptan.
    """
    boundary = "----BpmStartPro" + uuid.uuid4().hex

    head = b""
    for key, value in fields.items():
        head += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (boundary, key, value)).encode("utf-8")

    filename = os.path.basename(file_path).encode("ascii", "replace").decode("ascii")
    head += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n'
             % (boundary, file_field, filename)).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")

    file_size = os.path.getsize(file_path)

    def generator():
        yield head
        sent = 0
        last_emit = 0.0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                if on_progress:
                    now = time.time()
                    # Como mucho 4 avisos por segundo, para no saturar el socket.
                    if now - last_emit > 0.25 or sent >= file_size:
                        last_emit = now
                        try:
                            on_progress(sent, file_size)
                        except Exception:
                            pass
                yield chunk
        yield tail

    headers = {
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Content-Length": str(len(head) + file_size + len(tail)),
    }
    return generator(), headers


def bpmstart_create_separation(file_path, sep_type=40, output_format=1, add_opt1=None, add_opt2=None, add_opt3=None, text_prompt=None,
                                on_upload=None, on_status=None):
    api_key = get_user_key()
    if not api_key:
        return False, "No hay codigo BPMStartPRO configurado. Abre Ajustes y agrega tu codigo."

    url = "https://mvsep.com/api/separation/create"

    send_path = file_path
    temp_path = None
    try:
        ext = Path(file_path).suffix.lower()
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        # MVSEP upload limit is ~50MB. If file is not MP3 or is > 45MB, convert to 320k MP3 for upload.
        if ext != ".mp3" or file_size_mb > 45:
            ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
            temp_path = str(Path(file_path).with_name(Path(file_path).stem + "_upload_temp.mp3"))
            try:
                proc = subprocess.run(
                    [str(ffmpeg), "-y", "-i", str(file_path), "-vn", "-codec:a", "libmp3lame", "-b:a", "320k", temp_path],
                    capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception:
                return False, "Error al optimizar el audio para BpmStart Pro."
            if proc.returncode != 0 or not os.path.exists(temp_path):
                return False, "No se pudo optimizar el audio para BpmStart Pro."
            send_path = temp_path

        last_error = "Error al crear tarea."
        max_retries = 15

        for attempt in range(max_retries):
            if on_status and attempt > 0:
                on_status(f"Reintentando subida ({attempt + 1} de {max_retries})...", attempt)
            data = {
                "api_token": api_key,
                "sep_type": str(sep_type),
                "output_format": str(output_format),
                "is_demo": "0",
            }
            if text_prompt:
                # For TTS or text prompt-driven models (add_opt1 or add_opt2 text input)
                if add_opt1 is None:
                    data["add_opt1"] = text_prompt
                elif add_opt2 is None:
                    data["add_opt2"] = text_prompt
                else:
                    data["add_opt3"] = text_prompt

            if add_opt1 is not None and "add_opt1" not in data:
                data["add_opt1"] = str(add_opt1)
            if add_opt2 is not None and "add_opt2" not in data:
                data["add_opt2"] = str(add_opt2)
            if add_opt3 is not None and "add_opt3" not in data:
                data["add_opt3"] = str(add_opt3)

            # Cuerpo nuevo en cada intento: un generador solo se puede
            # recorrer una vez.
            body, up_headers = _build_multipart_stream(data, "audiofile", send_path, on_upload)

            try:
                # requests le pone "Transfer-Encoding: chunked" a cualquier
                # generador, y mandar eso junto con Content-Length es HTTP
                # invalido: nginx responde 400 y la separacion ni arranca. Por
                # eso se prepara la peticion a mano y se quita esa cabecera.
                prepared = requests.Request("POST", url, data=body, headers=up_headers).prepare()
                prepared.headers.pop("Transfer-Encoding", None)
                prepared.headers["Content-Length"] = up_headers["Content-Length"]
                resp = requests.Session().send(prepared, timeout=600)
            except requests.exceptions.Timeout:
                last_error = "Tiempo de espera agotado al subir a BpmStart Pro. Intenta de nuevo."
                time.sleep(5)
                continue
            except requests.exceptions.RequestException as e:
                last_error = f"Error de conexion con BpmStart Pro: {str(e)[:150]}"
                time.sleep(5)
                continue

            try:
                result = resp.json()
            except Exception:
                result = {}

            if result.get("success"):
                return True, result["data"]["hash"]

            errors = result.get("errors")
            if isinstance(errors, list) and errors:
                msg = errors[0]
            elif isinstance(errors, str):
                msg = errors
            else:
                msg = result.get("data", {}).get("message") or result.get("message") or result.get("error") or resp.text[:200] or "Error desconocido"

            # Si el servidor responde que ya hay un archivo procesandose en la cola, esperar y reintentar
            if "queue" in msg.lower() or "unprocessed" in msg.lower() or resp.status_code == 429:
                last_error = "Hay una tarea anterior aun procesandose en los servidores de BpmStart Pro. Esperando a que termine..."
                if on_status:
                    on_status(
                        f"Esperando turno en BpmStart Pro: hay otra tarea en proceso ({attempt + 1}/{max_retries})...",
                        attempt)
                time.sleep(10)
                continue

            # Ojo: un 401 NO siempre significa codigo invalido. El servidor
            # tambien responde 401 cuando rechaza la conexion por otros motivos
            # (proteccion antibot / huella TLS), y en esos casos reintentar si
            # funciona. Solo se da por invalido si el propio servidor lo dice.
            if resp.status_code == 401:
                low_msg = msg.lower()
                if "invalid token" in low_msg or ("token" in low_msg and "invalid" in low_msg):
                    return False, ("Tu codigo BPMStartPRO no es valido o expiro. "
                                   "Abre Ajustes y vuelve a pegarlo.")
                last_error = "BpmStart Pro rechazo la conexion. Reintentando..."
                if on_status:
                    on_status(f"El servidor rechazo la conexion, reintentando ({attempt + 1}/{max_retries})...", attempt)
                time.sleep(5)
                continue

            if resp.status_code != 200:
                return False, f"BpmStart Pro respondio HTTP {resp.status_code}: {msg}"

            return False, msg

        return False, "Hay una tarea anterior aun procesandose en el servidor. Por favor espera 1 minuto a que termine e intenta de nuevo."
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def bpmstart_get_status(task_hash):
    url = "https://mvsep.com/api/separation/get"
    params = {"hash": task_hash}
    resp = requests.get(url, params=params, timeout=60)
    return resp.json()


def bpmstart_get_queue_summary():
    api_key = get_user_key()
    if not api_key:
        return None
    try:
        url = "https://mvsep.com/api/app/queue/summary"
        resp = requests.get(url, params={"api_token": api_key}, timeout=8)
        data = resp.json()
        if data.get("success"):
            return data.get("data", {})
    except Exception:
        pass
    return None


def bpmstart_cancel_separation(task_hash):
    api_key = get_user_key()
    if not api_key:
        return False, "No hay código BPMStart Pro configurado."
    try:
        url = "https://mvsep.com/api/separation/cancel"
        resp = requests.post(url, data={"api_token": api_key, "hash": task_hash}, timeout=12)
        data = resp.json()
        return data.get("success", False), data.get("data", {}).get("message") or "Cancelado"
    except Exception as e:
        return False, str(e)


def mvsep_download_file(url, dest_path):
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)


SPANISH_LABELS = {
    "Vocals": "Vocales",
    "Bass": "Bajo",
    "Drums": "Bateria",
    "Guitar": "Guitarra",
    "Piano": "Piano",
    "Other": "Otro",
    "Instrum": "Instrumental",
    "Kick": "Kick",
    "Snare": "Snare",
    "HiHat": "HiHat",
    "Ride": "Ride",
    "Crash": "Crash",
    "Toms": "Toms",
    "Synth": "Sintetizador",
    "Keys": "Teclados",
    "Wind": "Vientos",
    "Percussion": "Percusion",
    "Vocals_Lead": "Vocales",
    "Vocals_Back": "Coros",
    "Crowd": "Publico",
    "Speech": "Discurso",
    "Music": "Musica",
    "Effects": "Efectos",
    "Strings": "Cuerdas",
    "Plucked_Strings": "Cuerdas Pulsadas",
    "Brass": "Latones",
    "Woodwind": "Maderas",
    "Saxophone": "Saxofon",
    "Flute": "Flauta",
    "Trumpet": "Trompeta",
    "Trombone": "Trombon",
    "Oboe": "Oboe",
    "Clarinet": "Clarinete",
    "French_Horn": "Corno Frances",
    "Harmonica": "Armonica",
    "Tuba": "Tuba",
    "Bassoon": "Fagot",
    "Bagpipes": "Gaita",
    "Whistle": "Silbato",
    "Organ": "Organo",
    "Harpsichord": "Clavicordio",
    "Accordion": "Acordeon",
    "Vibraphone": "Vibrafono",
    "Tambourine": "Pandereta",
    "Marimba": "Marimba",
    "Glockenspiel": "Glockenspiel",
    "Timpani": "Timpani",
    "Triangle": "Triangulo",
    "Congas": "Congas",
    "Bells": "Campanas",
    "Xylophone": "Xilofono",
    "Celesta": "Celesta",
    "Violin": "Violin",
    "Viola": "Viola",
    "Cello": "Violonchelo",
    "Double_Bass": "Contrabajo",
    "Harp": "Arpa",
    "Mandolin": "Mandolina",
    "Banjo": "Banjo",
    "Sitar": "Sitar",
    "Ukulele": "Ukelele",
    "Choir": "Coro",
    "Soprano": "Soprano",
    "Alto": "Alto",
    "Tenor": "Tenor",
    "Lead_Guitar": "Lead",
    "Rhythm_Guitar": "Ritmica",
    "Acoustic_Guitar": "Guitarra Acustica",
    "Electric_Guitar": "Guitarra Electrica",
    "Pedal_Steel": "Pedal Steel",
    "Digital_Piano": "Piano Digital",
    "Wind_Chimes": "Campanas de Viento",
    "Cowbell": "Cencerro",
    "Metal_Bars": "Campanas Metalicas",
    "Rhodes": "Rhodes",
    "Dobro": "Dobro",
}


def get_video_height(file_path):
    """Altura real del video en pixeles, o 0 si no se pudo determinar."""
    return get_video_resolution(file_path)[1]


def _discard_lowq(lowq_backup, lowq_dir):
    """Borra el respaldo de baja calidad cuando ya hay una descarga buena."""
    if not lowq_backup:
        return
    try:
        lowq_backup[0].unlink()
    except Exception:
        pass
    try:
        lowq_dir.rmdir()
    except Exception:
        pass


def get_audio_duration(file_path):
    ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
    try:
        res = subprocess.run(
            [str(ffmpeg), "-i", str(file_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if match:
            hours, mins, secs = match.groups()
            return int(hours) * 3600 + int(mins) * 60 + float(secs)
    except Exception as e:
        log(f"Error calculando duracion: {e}")
    return 0.0


def split_audio_into_chunks(file_path, temp_dir, chunk_duration=570, overlap=3):
    ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
    total_sec = get_audio_duration(file_path)
    if total_sec <= 600 or total_sec == 0:
        return [{"index": 0, "path": str(file_path), "is_single": True, "start": 0, "duration": total_sec}], total_sec

    chunks = []
    current_start = 0.0
    idx = 0
    os.makedirs(temp_dir, exist_ok=True)

    while current_start < total_sec:
        cur_dur = min(chunk_duration, total_sec - current_start)
        # Using 320k MP3 avoids the 50MB payload limit on MVSEP while maintaining full audio quality
        chunk_file = Path(temp_dir) / f"chunk_input_{idx}.mp3"

        cmd = [
            str(ffmpeg), "-y",
            "-ss", f"{current_start:.3f}",
            "-t", f"{cur_dur:.3f}",
            "-i", str(file_path),
            "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
            str(chunk_file)
        ]
        proc = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode != 0 or not chunk_file.exists():
            raise RuntimeError(f"Error al cortar el bloque {idx + 1} del audio.")

        chunks.append({
            "index": idx,
            "path": str(chunk_file),
            "is_single": False,
            "start": current_start,
            "duration": cur_dur
        })
        idx += 1
        current_start += (chunk_duration - overlap)
        if current_start >= total_sec:
            break

    return chunks, total_sec


def merge_stem_chunks(chunk_paths, output_path, overlap=3, output_format=1):
    if not chunk_paths:
        return False
    if len(chunk_paths) == 1:
        import shutil
        shutil.copy2(chunk_paths[0], output_path)
        return True

    ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
    inputs = []
    for p in chunk_paths:
        inputs.extend(["-i", str(p)])

    # Chain acrossfade filters:
    # [0:a][1:a]acrossfade=d=3:c1=tri:c2=tri[a1]; [a1][2:a]acrossfade=d=3:c1=tri:c2=tri[a2] ...
    filter_parts = []
    last_label = "0:a"
    for i in range(1, len(chunk_paths)):
        next_label = f"a{i}" if i < len(chunk_paths) - 1 else "out"
        filter_parts.append(f"[{last_label}][{i}:a]acrossfade=d={overlap}:c1=tri:c2=tri[{next_label}]")
        last_label = f"a{i}"

    filter_str = ";".join(filter_parts)

    ext_map = {
        0: ("libmp3lame", "320k"),
        1: ("pcm_s16le", None),
        2: ("flac", None),
        3: ("aac", "320k"),
        4: ("pcm_s24le", None),
        5: ("flac", None)
    }
    codec, bitrate = ext_map.get(output_format, ("libmp3lame", "320k"))

    cmd = [str(ffmpeg), "-y"] + inputs + ["-filter_complex", filter_str, "-map", "[out]", "-c:a", codec]
    if bitrate:
        cmd.extend(["-b:a", bitrate])
    cmd.append(str(output_path))

    proc = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return proc.returncode == 0 and os.path.exists(output_path)


@app.route("/api/categories")
def api_categories():
    result = {}
    for key, cat in CATEGORIES.items():
        entry = {"name": cat["name"], "icon": cat["icon"]}
        if "short" in cat:
            entry["short"] = cat["short"]
        if "short_desc" in cat:
            entry["short"] = cat["short_desc"]
        if cat.get("sub"):
            entry["sub"] = {
                sk: {"name": sv["name"]}
                for sk, sv in cat["sub"].items()
            }
        result[key] = entry
    return jsonify(result)


@socketio.on("start_separation")
def handle_separation(data):
    filename = data.get("filename", "")
    raw_fmt = data.get("output_format", 1)
    output_format = 1 if raw_fmt == "midi" else int(raw_fmt)
    category = data.get("category", "vocal")
    sub_category = data.get("sub_category", None)
    text_prompt = data.get("text_prompt", "").strip() or None

    if not filename:
        emit("sep_error", {"error": "No hay archivo para separar."})
        return

    cat = CATEGORIES.get(category)
    if not cat:
        emit("sep_error", {"error": f"Categoria desconocida: {category}"})
        return

    if cat.get("sub") and sub_category:
        model = cat["sub"].get(sub_category)
        if not model:
            emit("sep_error", {"error": f"Sub-modelo desconocido: {sub_category}"})
            return
        sep_type = model["sep_type"]
        add_opt1 = model.get("add_opt1")
        add_opt2 = model.get("add_opt2")
        add_opt3 = model.get("add_opt3")
        labels = model.get("labels") or cat.get("labels")
    else:
        sep_type = cat.get("sep_type")
        add_opt1 = cat.get("add_opt1")
        add_opt2 = cat.get("add_opt2")
        add_opt3 = cat.get("add_opt3")
        labels = cat.get("labels")
        if not sep_type:
            emit("sep_error", {"error": f"Debe seleccionar un sub-modelo para '{cat.get('name', category)}'"})
            return

    file_path = SEPARATIONS_DIR / filename
    if not file_path.exists():
        emit("sep_error", {"error": "Archivo no encontrado en el servidor."})
        return

    song_name = Path(filename).stem
    sep_id = song_name
    folder_name = song_name
    sep_folder = SEPARATIONS_DIR / folder_name
    counter = 1
    while sep_folder.exists():
        folder_name = f"{song_name} ({counter})"
        sep_folder = SEPARATIONS_DIR / folder_name
        counter += 1

    active_separations[sep_id] = {"hash": None, "status": "uploading"}

    emit("sep_started", {"sep_id": sep_id})

    def run_separation():
        from concurrent.futures import ThreadPoolExecutor
        temp_chunks_dir = sep_folder / "_temp_chunks"
        try:
            chunks, total_sec = split_audio_into_chunks(file_path, temp_chunks_dir, chunk_duration=570, overlap=3)
            num_chunks = len(chunks)

            ext_map = {0: "mp3", 1: "wav", 2: "flac", 3: "m4a", 4: "wav", 5: "flac"}
            file_ext = ext_map.get(output_format, "mp3")
            is_single_file = labels is None

            collected_stems = {}
            stem_metadata = {}

            for chunk_idx, chunk_info in enumerate(chunks):
                if active_separations.get(sep_id, {}).get("cancelled"):
                    log(f"Separacion {sep_id} cancelada antes del chunk {chunk_idx + 1}")
                    return

                part_prefix = f"Parte {chunk_idx + 1}/{num_chunks}: " if num_chunks > 1 else ""
                base_pct = int((chunk_idx / num_chunks) * 75)
                chunk_slice_pct = int(75 / num_chunks)

                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "stage": "preparar",
                    "status": "subiendo",
                    "message": f"{part_prefix}Subiendo archivo a BPMStart Pro...",
                    "percent": max(5, base_pct + int(chunk_slice_pct * 0.1)),
                    "task_hash": None,
                })

                # La subida ocupa la franja 0.1 -> 0.3 del tramo de este chunk.
                upload_start = base_pct + int(chunk_slice_pct * 0.1)
                upload_end = base_pct + int(chunk_slice_pct * 0.3)

                def _on_upload(sent, total, _pfx=part_prefix, _a=upload_start, _b=upload_end):
                    frac = sent / total if total else 0
                    if total >= 1048576:
                        tam = "%.1f de %.1f MB" % (sent / 1048576.0, total / 1048576.0)
                    else:
                        tam = "%d de %d KB" % (sent / 1024, total / 1024)
                    socketio.emit("sep_progress", {
                        "sep_id": sep_id,
                        "stage": "preparar",
                        "status": "subiendo",
                        "message": "%sSubiendo a BPMStart Pro... %d%% (%s)" % (
                            _pfx, int(frac * 100), tam),
                        "percent": max(5, _a + int((_b - _a) * frac)),
                        "task_hash": None,
                    })

                def _on_status(texto, intento, _pfx=part_prefix, _a=upload_start):
                    socketio.emit("sep_progress", {
                        "sep_id": sep_id,
                        "stage": "preparar",
                        "status": "subiendo",
                        "message": f"{_pfx}{texto}",
                        "percent": max(5, _a),
                        "task_hash": None,
                    })

                success, result = bpmstart_create_separation(
                    chunk_info["path"],
                    sep_type=sep_type,
                    output_format=output_format,
                    add_opt1=add_opt1,
                    add_opt2=add_opt2,
                    add_opt3=add_opt3,
                    text_prompt=text_prompt,
                    on_upload=_on_upload,
                    on_status=_on_status,
                )

                if not success:
                    socketio.emit("sep_error", {
                        "sep_id": sep_id,
                        "error": (f"Error al crear tarea ({part_prefix.strip().rstrip(':')}): {result}"
                                  if part_prefix.strip() else f"Error al crear tarea: {result}"),
                    })
                    return

                task_hash = result
                active_separations[sep_id]["hash"] = task_hash

                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "stage": "fila",
                    "status": "procesando",
                    "message": f"{part_prefix}Procesando con BPMStart Pro...",
                    "percent": base_pct + int(chunk_slice_pct * 0.3),
                    "task_hash": task_hash,
                })

                max_wait = 1800
                elapsed = 0
                poll_interval = 10
                chunk_files_data = None

                while elapsed < max_wait:
                    time.sleep(poll_interval)
                    elapsed += poll_interval

                    if active_separations.get(sep_id, {}).get("cancelled"):
                        bpmstart_cancel_separation(task_hash)
                        log(f"[CANCEL] Tarea {task_hash} cancelada durante polling.")
                        return

                    try:
                        status_resp = bpmstart_get_status(task_hash)
                    except Exception:
                        continue

                    if not status_resp.get("success"):
                        socketio.emit("sep_error", {
                            "sep_id": sep_id,
                            "error": f"Error consultando estado ({part_prefix.strip()}).",
                        })
                        return

                    status = status_resp.get("status", "")

                    if status == "waiting":
                        # En cola: obtener resumen para tiempo estimado y posicion
                        q_summary = bpmstart_get_queue_summary() or {}
                        wait_sec = q_summary.get("estimated_wait_seconds")
                        c_order = status_resp.get("data", {}).get("current_order", 0)
                        q_total = status_resp.get("data", {}).get("queue_count") or q_summary.get("ahead", 0)
                        if wait_sec is not None:
                            wait_str = f"{int(wait_sec) // 60}:{int(wait_sec) % 60:02d}"
                        else:
                            wait_str = f"0:{max(15, (q_total or 1) * 20):02d}" if (q_total or 0) > 0 else "1:30"

                        socketio.emit("sep_progress", {
                            "sep_id": sep_id,
                            "stage": "fila",
                            "status": "En cola",
                            "message": f"{part_prefix}Número {c_order} de {q_total} en la cola",
                            "percent": base_pct + int(chunk_slice_pct * 0.35),
                            "wait_seconds": wait_sec,
                            "wait_time": wait_str,
                            "queue_order": c_order,
                            "queue_total": q_total,
                            "task_hash": task_hash,
                        })

                    elif status in ("processing", "distributing"):
                        sub_pct = 0.45
                        detail_msg = f"{part_prefix}Separando tu audio ahora mismo con BPMStart Pro..."
                        if status == "distributing":
                            finished = status_resp.get("data", {}).get("finished_chunks", 0)
                            total = status_resp.get("data", {}).get("all_chunks", 1)
                            sub_pct = 0.35 + (0.35 * finished / total) if total else 0.45
                            detail_msg = f"{part_prefix}Separando en GPUs de BPMStart Pro (parte {finished}/{total})..."

                        socketio.emit("sep_progress", {
                            "sep_id": sep_id,
                            "stage": "separar",
                            "status": "Separando",
                            "message": detail_msg,
                            "percent": base_pct + int(chunk_slice_pct * sub_pct),
                            "task_hash": task_hash,
                        })

                    elif status == "merging":
                        socketio.emit("sep_progress", {
                            "sep_id": sep_id,
                            "stage": "unir",
                            "status": "Uniendo",
                            "message": f"{part_prefix}Uniendo y ensamblando pistas de audio...",
                            "percent": base_pct + int(chunk_slice_pct * 0.78),
                            "task_hash": task_hash,
                        })

                    elif status == "done":
                        chunk_files_data = status_resp.get("data", {}).get("files", [])
                        break

                    elif status == "failed":
                        error_msg = status_resp.get("data", {}).get("message", "Error desconocido")
                        socketio.emit("sep_error", {
                            "sep_id": sep_id,
                            "error": f"Fallo en la separacion ({part_prefix.strip()}): {error_msg}",
                        })
                        return

                if not chunk_files_data:
                    socketio.emit("sep_error", {
                        "sep_id": sep_id,
                        "error": f"No se encontraron archivos de resultado ({part_prefix.strip()}).",
                    })
                    return

                chunk_dest_dir = temp_chunks_dir / f"chunk_{chunk_idx}" if num_chunks > 1 else sep_folder
                chunk_dest_dir.mkdir(parents=True, exist_ok=True)

                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "stage": "unir",
                    "status": "Descargando",
                    "message": f"{part_prefix}Descargando {len(chunk_files_data)} pistas en paralelo...",
                    "percent": base_pct + int(chunk_slice_pct * 0.85),
                    "task_hash": task_hash,
                })

                def download_single_file(file_info):
                    stem_url = file_info.get("url", "")
                    if not stem_url:
                        return None

                    download_name = file_info.get("download", "")
                    real_url_ext = Path(download_name).suffix.lstrip(".").lower() if download_name else Path(stem_url.split("?")[0]).suffix.lstrip(".").lower()
                    current_ext = real_url_ext if real_url_ext in ("mid", "midi", "txt", "zip") else file_ext
                    if category == "midi":
                        current_ext = "mid"

                    stem_type = file_info.get("type") or ""
                    label_esp = SPANISH_LABELS.get(stem_type, stem_type)

                    if is_single_file:
                        stem_name = Path(download_name).stem if download_name else Path(stem_url.split("?")[0]).stem
                        if stem_name == Path(file_path).stem or not stem_name:
                            new_name = f"{song_name}_resultado.{current_ext}"
                        else:
                            new_name = f"{stem_name}.{current_ext}"
                        label = stem_name or song_name
                    else:
                        if stem_type:
                            label = label_esp if label_esp else stem_type
                        else:
                            url_filename = stem_url.split("?")[0].split("/")[-1]
                            label = Path(url_filename).stem
                        new_name = f"{label}.{current_ext}"

                    dest_file = chunk_dest_dir / new_name
                    mvsep_download_file(stem_url, str(dest_file))
                    return new_name, dest_file, label, current_ext

                with ThreadPoolExecutor(max_workers=min(len(chunk_files_data), 6)) as executor:
                    download_results = list(executor.map(download_single_file, chunk_files_data))

                for res in download_results:
                    if res:
                        new_name, dest_file, label, current_ext = res
                        if new_name not in collected_stems:
                            collected_stems[new_name] = []
                            stem_metadata[new_name] = {"label": label, "ext": current_ext}
                        collected_stems[new_name].append(dest_file)

            sep_folder.mkdir(exist_ok=True)
            stems = []

            if num_chunks > 1:
                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "stage": "unir",
                    "status": "fusionando",
                    "message": "Fusión perfecta de pistas de audio (Crossfade)...",
                    "percent": 88,
                    "task_hash": None,
                })

                for new_name, chunk_file_list in collected_stems.items():
                    final_dest = sep_folder / new_name
                    meta = stem_metadata[new_name]
                    if meta["ext"] in ("mid", "midi", "txt", "zip"):
                        import shutil
                        shutil.copy2(str(chunk_file_list[0]), str(final_dest))
                    else:
                        merge_stem_chunks(chunk_file_list, final_dest, overlap=3, output_format=output_format)

                    stems.append({
                        "filename": f"{folder_name}/{new_name}",
                        "label": meta["label"],
                        "ext": meta["ext"],
                    })
            else:
                for new_name in collected_stems:
                    meta = stem_metadata[new_name]
                    stems.append({
                        "filename": f"{folder_name}/{new_name}",
                        "label": meta["label"],
                        "ext": meta["ext"],
                    })

            original_dest = sep_folder / file_path.name
            if not original_dest.exists():
                import shutil
                shutil.copy2(str(file_path), str(original_dest))

            if temp_chunks_dir.exists():
                import shutil
                shutil.rmtree(str(temp_chunks_dir), ignore_errors=True)

            socketio.emit("sep_complete", {
                "sep_id": sep_id,
                "folder": folder_name,
                "original": f"{folder_name}/{file_path.name}",
                "stems": stems,
            })

        except Exception as e:
            if temp_chunks_dir.exists():
                import shutil
                shutil.rmtree(str(temp_chunks_dir), ignore_errors=True)
            socketio.emit("sep_error", {
                "sep_id": sep_id,
                "error": f"Error inesperado: {str(e)[:200]}",
            })

    thread = threading.Thread(target=run_separation, daemon=True)
    thread.start()


@socketio.on("cancel_separation")
def handle_cancel_separation(data):
    sep_id = data.get("sep_id") if isinstance(data, dict) else None
    task_hash = data.get("task_hash") if isinstance(data, dict) else None
    log(f"[CANCEL] Solicitud de cancelación recibida para sep_id={sep_id}, task_hash={task_hash}")
    if sep_id and sep_id in active_separations:
        active_separations[sep_id]["cancelled"] = True
        if not task_hash:
            task_hash = active_separations[sep_id].get("hash")
    if task_hash:
        success, msg = bpmstart_cancel_separation(task_hash)
        log(f"[CANCEL] Servidor respondió: success={success}, msg={msg}")
    emit("sep_cancelled", {
        "sep_id": sep_id,
        "message": "Separación cancelada por el usuario."
    })


def semitones_to_pitch_factor(semitones):
    """factor = 2^(semitonos/12), igual que en la version de Android."""
    return 2.0 ** (float(semitones) / 12.0)


@socketio.on("start_pitch_export")
def handle_pitch_export(data):
    filename = (data.get("filename") or "").strip()
    try:
        semitones = max(-12.0, min(12.0, float(data.get("semitones") or 0)))
    except (TypeError, ValueError):
        semitones = 0.0
    try:
        speed = max(0.5, min(2.0, float(data.get("speed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    out_format = (data.get("format") or "mp3").lower()
    if out_format not in ("mp3", "wav", "flac"):
        out_format = "mp3"

    if not filename:
        emit("pitch_error", {"error": "Selecciona un archivo de audio."})
        return

    source = SEPARATIONS_DIR / filename
    if not source.exists():
        source = DOWNLOADS_DIR / filename
    if not source.exists():
        emit("pitch_error", {"error": f"Archivo no encontrado: {filename}"})
        return

    if abs(semitones) < 0.01 and abs(speed - 1.0) < 0.01:
        emit("pitch_error", {"error": "Ajusta el tono o la velocidad antes de exportar."})
        return

    emit("pitch_started", {})

    def run_export():
        try:
            stem = Path(source).stem
            tag_parts = []
            if abs(semitones) >= 0.01:
                tag_parts.append(("+" if semitones > 0 else "") + ("%g" % round(semitones, 1)) + "st")
            if abs(speed - 1.0) >= 0.01:
                tag_parts.append("%gx" % round(speed, 2))
            out_name = sanitize_filename(f"{stem} ({' '.join(tag_parts)}).{out_format}")
            out_path = PITCH_DIR / out_name

            ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
            pitch_factor = semitones_to_pitch_factor(semitones)

            # rubberband cambia el tono sin alterar la duracion (y la velocidad
            # sin alterar el tono), que es justo lo que hace la app de Android.
            af = "rubberband=pitch=%.6f:tempo=%.6f" % (pitch_factor, speed)

            cmd = [str(ffmpeg), "-y", "-i", str(source), "-vn", "-af", af]
            if out_format == "mp3":
                cmd += ["-c:a", "libmp3lame", "-b:a", "320k"]
            elif out_format == "flac":
                cmd += ["-c:a", "flac"]
            else:
                cmd += ["-c:a", "pcm_s16le"]
            cmd += ["-progress", "pipe:1", "-nostats", str(out_path)]

            total_sec = get_audio_duration(source) or 0.0

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, bufsize=1, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            last_pct = -1
            tail = []
            for line in proc.stdout:
                line = line.strip()
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
                if line.startswith("out_time_ms=") and total_sec > 0:
                    try:
                        done_sec = int(line.split("=", 1)[1]) / 1_000_000.0
                    except (ValueError, IndexError):
                        continue
                    pct = int(min(99.0, (done_sec / total_sec) * 100.0))
                    if pct != last_pct and pct >= 0:
                        last_pct = pct
                        socketio.emit("pitch_progress", {
                            "percent": pct,
                            "message": f"Procesando tonalidad... {pct}%"
                        })

            code = proc.wait()
            if code != 0 or not out_path.exists():
                log("[TONO] ffmpeg fallo (%s): %s" % (code, "\n".join(tail[-8:])))
                socketio.emit("pitch_error", {"error": "No se pudo procesar el audio."})
                return

            socketio.emit("pitch_complete", {
                "filename": out_name,
                "url": f"/download_pitch/{out_name}",
                "semitones": round(semitones, 1),
                "speed": round(speed, 2),
                "size_mb": round(out_path.stat().st_size / 1024 / 1024, 1),
            })
        except Exception as e:
            log(f"[TONO] Error: {e}\n{traceback.format_exc()}")
            socketio.emit("pitch_error", {"error": f"Error inesperado: {str(e)[:200]}"})

    threading.Thread(target=run_export, daemon=True).start()


PREVIEW_CACHE_DIR = PITCH_DIR / ".preview"
PREVIEW_SECONDS = 25


@app.route("/pitch_preview")
def pitch_preview():
    """Fragmento corto ya procesado, para escuchar el tono antes de exportar.

    Usa el mismo rubberband que la exportacion, asi que lo que se oye aqui es
    exactamente lo que va a salir en el archivo final.
    """
    filename = (request.args.get("filename") or "").strip()
    try:
        semitones = max(-12.0, min(12.0, float(request.args.get("semitones") or 0)))
    except (TypeError, ValueError):
        semitones = 0.0
    try:
        speed = max(0.5, min(2.0, float(request.args.get("speed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0

    if not filename:
        return jsonify({"error": "Falta el archivo"}), 400

    source = SEPARATIONS_DIR / filename
    if not source.exists():
        source = DOWNLOADS_DIR / filename
    if not source.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404

    PREVIEW_CACHE_DIR.mkdir(exist_ok=True)
    key = "%s_%s_%s" % (sanitize_filename(Path(filename).stem)[:40],
                        ("%+.1f" % semitones).replace(".", "p"),
                        ("%.2f" % speed).replace(".", "p"))
    cached = PREVIEW_CACHE_DIR / (key + ".mp3")

    if not cached.exists():
        total = get_audio_duration(source) or 0.0
        # Arrancar un poco adentro de la cancion: el inicio suele ser silencio
        # o intro, y para juzgar la tonalidad conviene una parte con cuerpo.
        start = 0.0
        if total > PREVIEW_SECONDS + 10:
            start = min(total * 0.25, max(0.0, total - PREVIEW_SECONDS))

        ffmpeg = Path(get_ffmpeg_dir()) / "ffmpeg.exe"
        af = "rubberband=pitch=%.6f:tempo=%.6f" % (semitones_to_pitch_factor(semitones), speed)
        cmd = [str(ffmpeg), "-y", "-v", "error",
               "-ss", "%.3f" % start, "-t", str(PREVIEW_SECONDS),
               "-i", str(source), "-vn", "-af", af,
               "-c:a", "libmp3lame", "-b:a", "160k", str(cached)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                 encoding="utf-8", errors="replace",
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return jsonify({"error": "La vista previa tardo demasiado"}), 504
        if res.returncode != 0 or not cached.exists():
            log("[TONO] Vista previa fallo: %s" % (res.stderr or "")[-200:])
            return jsonify({"error": "No se pudo generar la vista previa"}), 500

    return send_file(cached, mimetype="audio/mpeg", conditional=True)


@app.route("/download_pitch/<path:filename>")
def download_pitch(filename):
    file_path = PITCH_DIR / filename
    if file_path.exists():
        mime = MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")
        return send_file(file_path, as_attachment=True,
                         download_name=file_path.name, mimetype=mime)
    return jsonify({"error": "Archivo no encontrado"}), 404


@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    if "file" not in request.files:
        return jsonify({"error": "No se envio ningun archivo"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".wma"):
        return jsonify({"error": f"Formato no soportado: {ext}"}), 400

    safe_name = sanitize_filename(Path(file.filename).stem) + ext
    save_path = SEPARATIONS_DIR / safe_name
    file.save(str(save_path))

    return jsonify({"filename": safe_name, "size": save_path.stat().st_size})


MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
}


@app.route("/download_stem/<path:filename>")
def download_stem(filename):
    file_path = SEPARATIONS_DIR / filename
    if file_path.exists():
        ext = file_path.suffix.lower()
        mime = MIME_MAP.get(ext, "application/octet-stream")
        return send_file(file_path, as_attachment=True,
                         download_name=file_path.name,
                         mimetype=mime)
    return jsonify({"error": "Stem no encontrado"}), 404


# ========================
#  MIDI PRO PIPELINE
# ========================

@socketio.on("start_midi_pipeline")
def handle_start_midi_pipeline(data):
    url = (data.get("url") or "").strip()
    filename = (data.get("filename") or "").strip()
    mode = data.get("mode", "multitrack_7")
    custom_bpm = data.get("custom_bpm")

    if not url and not filename:
        emit("midi_error", {"error": "Debes ingresar un enlace de YouTube o seleccionar un archivo de audio."})
        return

    def run_pipeline():
        import zipfile
        import shutil

        try:
            # Primera vez que se usa MIDI Pro en esta sesion: cargar el motor
            # (librosa/basic-pitch) tarda unos segundos, asi que se avisa.
            if midi_engine is None:
                socketio.emit("midi_progress", {
                    "status": "preparando",
                    "percent": 3,
                    "message": "Preparando el motor de transcripcion MIDI (solo la primera vez)..."
                })
            try:
                load_midi_engine()
            except Exception as e:
                log(f"[MIDI] No se pudo cargar el motor: {e}\n{traceback.format_exc()}")
                socketio.emit("midi_error", {"error": (
                    "No se pudo iniciar el motor de transcripcion MIDI. "
                    "Reinstala el programa si el problema persiste.")})
                return

            # Step 1: Download or locate audio file
            if url:
                socketio.emit("midi_progress", {
                    "status": "descargando",
                    "percent": 8,
                    "message": "Descargando audio de YouTube en alta fidelidad..."
                })
                ffmpeg_dir = get_ffmpeg_dir()
                ytdlp = get_ytdlp_path()
                js_runtime = get_js_runtime_arg()
                # "is not None": una lista vacia = el analisis confirmo que no hacen
                # falta cookies (ver el comentario de _working_cookies).
                download_cookies = list(_working_cookies) if _working_cookies is not None else all_cookies_arg()

                # Este pipeline es independiente de handle_download(), asi que
                # necesita las mismas defensas contra el bloqueo de YouTube:
                # runtime JS (si no, cae al progresivo viejo o da 403), varios
                # clientes de reserva, y timeout (sin uno, un yt-dlp colgado
                # dejaba la barra congelada en 8% para siempre, sin error).
                # Empezar por el cliente que ya funciono antes (lo cachea el
                # analisis de la pestana Descargar). Sin esto siempre se probaba
                # "default" primero, que hoy casi siempre da 403, y el usuario se
                # comia ~45 segundos de espera en 8% antes del primer intento util.
                global _working_client, _cookies_disabled
                clients_order = list(YT_CLIENTS)
                if _working_client in clients_order:
                    clients_order.remove(_working_client)
                    clients_order.insert(0, _working_client)

                song_title = None
                dl_proc = None
                last_dl_err = "Error desconocido"
                for attempt_idx, client in enumerate(clients_order):
                    # El aviso va ANTES de buscar el titulo: esa consulta puede
                    # tardar hasta 30s y antes se hacia en silencio, dejando la
                    # barra quieta en 8% sin que el usuario supiera si avanzaba.
                    log(f"[MIDI] Intento {attempt_idx + 1}/{len(clients_order)} con client {client}")
                    socketio.emit("midi_progress", {
                        "status": "descargando",
                        "percent": 8,
                        "message": f"Conectando con YouTube (intento {attempt_idx + 1}/{len(clients_order)})..."
                    })

                    if song_title is None:
                        title_cmd = ([ytdlp, "--get-title", "--no-warnings"] + js_runtime
                                     + download_cookies + client_arg(client) + [url])
                        try:
                            t_proc = subprocess.run(title_cmd, capture_output=True, text=True,
                                                     encoding="utf-8", errors="replace",
                                                     creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                            # Cookies del navegador bloqueadas: quitarlas ya, antes de
                            # la descarga de este mismo intento, y repetir la consulta.
                            if t_proc.returncode != 0 and download_cookies and is_cookie_error(t_proc.stderr):
                                _cookies_disabled = True
                                download_cookies = []
                                log("[MIDI] Cookies del navegador no legibles; se sigue sin ellas")
                                title_cmd = ([ytdlp, "--get-title", "--no-warnings"] + js_runtime
                                             + client_arg(client) + [url])
                                t_proc = subprocess.run(title_cmd, capture_output=True, text=True,
                                                         encoding="utf-8", errors="replace",
                                                         creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                            if t_proc.returncode == 0 and t_proc.stdout.strip():
                                song_title = sanitize_filename(t_proc.stdout.strip())
                        except subprocess.TimeoutExpired:
                            log(f"[MIDI] Consulta de titulo agotada con client {client}")
                    if song_title is None:
                        song_title = f"Audio_{int(time.time())}"

                    target_file = DOWNLOADS_DIR / f"{song_title}.mp3"
                    dl_cmd = ([ytdlp, "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
                               "--ffmpeg-location", ffmpeg_dir, "-o", str(DOWNLOADS_DIR / f"{song_title}.%(ext)s"),
                               "--no-playlist"] + js_runtime + download_cookies + client_arg(client) + [url])

                    # Se lee la salida en streaming (no con subprocess.run) por dos
                    # razones: para mostrar el avance real de la descarga en la barra
                    # -- antes todo este paso reportaba un 8% fijo y parecia colgado --
                    # y para poder detectar que yt-dlp se quedo sin responder.
                    dl_proc = subprocess.Popen(
                        dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        universal_newlines=True, bufsize=1, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )

                    pump = {"last": time.time(), "lines": []}

                    def _pump_output(proc_ref, state):
                        try:
                            for out_line in proc_ref.stdout:
                                state["last"] = time.time()
                                state["lines"].append(out_line.rstrip())
                        except Exception:
                            pass

                    threading.Thread(target=_pump_output, args=(dl_proc, pump), daemon=True).start()

                    STALL_SECONDS = 180
                    MAX_DOWNLOAD_SECONDS = 900
                    started_at = time.time()
                    seen = 0
                    last_pct_sent = -1
                    stalled = False

                    while True:
                        lines = pump["lines"]
                        while seen < len(lines):
                            out_line = lines[seen]
                            seen += 1
                            prog = parse_progress(out_line)
                            if "percent" in prog:
                                try:
                                    raw_pct = float(prog["percent"])
                                except (TypeError, ValueError):
                                    continue
                                # La descarga ocupa la franja 8-17% del total.
                                mapped = 8 + int(raw_pct * 0.09)
                                if mapped != last_pct_sent:
                                    last_pct_sent = mapped
                                    socketio.emit("midi_progress", {
                                        "status": "descargando",
                                        "percent": mapped,
                                        "message": f"Descargando audio de YouTube... {raw_pct:.0f}%"
                                    })
                            elif "[ExtractAudio]" in out_line:
                                socketio.emit("midi_progress", {
                                    "status": "descargando",
                                    "percent": 17,
                                    "message": "Convirtiendo el audio a MP3 de alta calidad..."
                                })

                        if dl_proc.poll() is not None and seen >= len(pump["lines"]):
                            break

                        now = time.time()
                        if now - pump["last"] > STALL_SECONDS or now - started_at > MAX_DOWNLOAD_SECONDS:
                            stalled = True
                            try:
                                dl_proc.kill()
                            except Exception:
                                pass
                            break
                        time.sleep(0.4)

                    returncode = dl_proc.wait()
                    full_out = "\n".join(pump["lines"][-40:])

                    if stalled:
                        last_dl_err = "YouTube dejo de responder durante la descarga."
                        log(f"[MIDI] Descarga sin respuesta con client {client}, cancelada")
                        continue

                    if returncode == 0 and target_file.exists():
                        _working_client = client
                        break
                    candidates = list(DOWNLOADS_DIR.glob(f"{song_title}.*"))
                    if returncode == 0 and candidates:
                        target_file = candidates[0]
                        _working_client = client
                        break
                    # Fallo por las cookies del navegador, no por YouTube: se
                    # quitan y se repite este mismo cliente. Insertarlo justo a
                    # continuacion hace que el for lo vuelva a recorrer (el
                    # iterador de listas avanza por posicion). Solo pasa una vez,
                    # porque despues download_cookies queda vacio.
                    if download_cookies and is_cookie_error(full_out):
                        _cookies_disabled = True
                        download_cookies = []
                        log(f"[MIDI] Cookies del navegador no legibles; se repite el client {client} sin ellas")
                        clients_order.insert(attempt_idx + 1, client)
                        continue

                    last_dl_err = full_out.strip()[-300:] if full_out.strip() else last_dl_err
                    log(f"[MIDI] Descarga fallo con client {client}: {last_dl_err[-200:]}")
                else:
                    if is_cookie_error(last_dl_err):
                        socketio.emit("midi_error", {"error": cookie_error_message(last_dl_err)})
                        return
                    msg = f"No se pudo descargar el audio de YouTube: {last_dl_err}"
                    if is_blocked(last_dl_err):
                        msg += " (YouTube esta bloqueando la descarga. Intenta de nuevo en unos minutos.)"
                    socketio.emit("midi_error", {"error": msg[:400]})
                    return
                source_audio_path = target_file
            else:
                source_audio_path = SEPARATIONS_DIR / filename
                if not source_audio_path.exists():
                    source_audio_path = DOWNLOADS_DIR / filename
                if not source_audio_path.exists():
                    socketio.emit("midi_error", {"error": f"Archivo de audio no encontrado: {filename}"})
                    return
                song_title = Path(source_audio_path).stem

            # Create unique project directory
            safe_project_name = sanitize_filename(song_title)
            project_folder = MIDI_PROJECTS_DIR / safe_project_name
            counter = 1
            while project_folder.exists():
                project_folder = MIDI_PROJECTS_DIR / f"{safe_project_name} ({counter})"
                counter += 1

            stems_dir = project_folder / "stems"
            midi_dir = project_folder / "midi_stems"
            stems_dir.mkdir(parents=True, exist_ok=True)
            midi_dir.mkdir(parents=True, exist_ok=True)

            # Step 2: BPM & Grid Detection
            socketio.emit("midi_progress", {
                "status": "bpm",
                "percent": 18,
                "message": "Detectando BPM exacto y rejilla de tiempo..."
            })
            
            detected_bpm, beat_times = midi_engine.detect_bpm_and_beats(source_audio_path)
            active_bpm = float(custom_bpm) if custom_bpm and float(custom_bpm) > 20 else detected_bpm
            log(f"[MIDI] BPM detectado: {detected_bpm}, BPM usado: {active_bpm}")

            # Step 3: Separation via BpmStart Pro API
            socketio.emit("midi_progress", {
                "status": "separando",
                "percent": 25,
                "message": "Enviando audio a los servidores de BpmStart Pro para separacion de instrumentos..."
            })

            if mode == "drums_only":
                sep_type = 37
                labels = ["Kick", "Snare", "HiHat", "Ride", "Crash", "Toms"]
            elif mode == "vocal_4":
                sep_type = 20
                labels = ["Vocales", "Bajo", "Bateria", "Otro"]
            else:
                sep_type = 63
                labels = ["Vocales", "Bajo", "Bateria", "Guitarra", "Piano", "Otro"]

            # FLAC en vez de WAV: es sin perdida igual (la transcripcion a MIDI
            # no pierde nada de precision) pero pesa cerca de la mitad, asi que
            # las pistas se descargan bastante mas rapido.
            STEM_EXT = "flac"
            success, task_hash = bpmstart_create_separation(
                str(source_audio_path),
                sep_type=sep_type,
                output_format=2
            )

            if not success:
                socketio.emit("midi_error", {"error": f"Error al iniciar separacion en BpmStart Pro: {task_hash}"})
                return

            max_wait = 1800
            start_wait = time.time()
            download_links = None
            
            while time.time() - start_wait < max_wait:
                time.sleep(4)
                try:
                    st_data = bpmstart_get_status(task_hash)
                except Exception:
                    continue

                if not st_data.get("success"):
                    continue

                # La API real devuelve el estado como texto en la raiz de la
                # respuesta ("waiting"/"processing"/"distributing"/"merging"/
                # "done"/"failed"), no un codigo numerico dentro de "data".
                # El codigo anterior comparaba contra numeros que la API nunca
                # manda, asi que nunca detectaba que la separacion ya habia
                # terminado y se quedaba esperando para siempre.
                status = st_data.get("status", "")
                if status in ("waiting", "processing", "distributing", "merging"):
                    pct = 32 if status == "waiting" else 48 if status == "processing" else 55
                    wait_sec = None
                    c_order = 0
                    q_total = 0
                    msg = "Separando pistas de audio con inteligencia artificial..."
                    if status == "waiting":
                        q_summary = bpmstart_get_queue_summary() or {}
                        wait_sec = q_summary.get("estimated_wait_seconds")
                        c_order = st_data.get("data", {}).get("current_order", 0)
                        q_total = st_data.get("data", {}).get("queue_count") or q_summary.get("ahead", 0)
                        msg = f"En cola de BPMStart Pro (Puesto {c_order} de {q_total})..."

                    socketio.emit("midi_progress", {
                        "status": "cola" if status == "waiting" else "procesando",
                        "stage": "fila" if status == "waiting" else "separar",
                        "percent": pct,
                        "message": msg,
                        "wait_seconds": wait_sec,
                        "queue_order": c_order,
                        "queue_total": q_total,
                    })
                elif status == "done":
                    download_links = st_data.get("data", {}).get("files", [])
                    break
                elif status == "failed":
                    err_msg = st_data.get("data", {}).get("message") or "Error en el servidor de separacion."
                    socketio.emit("midi_error", {"error": err_msg})
                    return

            if not download_links:
                socketio.emit("midi_error", {"error": "Tiempo de espera agotado en la separacion de audio."})
                return

            # Download separated stems
            socketio.emit("midi_progress", {
                "status": "descargando_stems",
                "percent": 65,
                "message": "Descargando stems de audio separados en alta calidad..."
            })

            # BpmStart Pro nombra sus archivos con abreviaturas tecnicas (p.ej.
            # "hh" para hihat) que no coinciden con nuestras etiquetas en
            # español/ingles completo, dejando esos stems con el nombre de
            # archivo crudo (larguisimo, con hash) en vez de uno legible.
            LABEL_ALIASES = {
                "Kick": ["kick", "bombo", "bd"],
                "Snare": ["snare", "caja", "sd"],
                "HiHat": ["hihat", "hi-hat", "hi_hat", "hh"],
                "Ride": ["ride"],
                "Crash": ["crash"],
                "Toms": ["toms", "tom"],
                "Vocales": ["vocal", "voz"],
                "Bajo": ["bass", "bajo"],
                "Bateria": ["drum", "bateria"],
                "Guitarra": ["guitar", "guitarra"],
                "Piano": ["piano", "keys", "teclado"],
                "Otro": ["other", "otro"],
            }

            local_stems = {}
            def label_for(item):
                raw_name = item.get("name") or os.path.basename(item.get("url") or "")
                raw_lower = raw_name.lower()
                for lbl in labels:
                    aliases = LABEL_ALIASES.get(lbl, [lbl.lower()])
                    if any(alias in raw_lower for alias in aliases):
                        return lbl
                # Stem extra que BpmStart Pro devuelve pero no pedimos
                # explicitamente (p.ej. la mezcla completa de bateria o el
                # "residual"): en vez del nombre de archivo crudo con hash,
                # usamos su ultimo segmento como nombre legible.
                tail = Path(raw_name).stem.rsplit("_", 1)[-1]
                return tail.capitalize() if tail else Path(raw_name).stem

            # Se asignan los nombres antes de bajar nada, para poder resolver
            # duplicados sin que dos pistas se pisen el archivo.
            planned = []
            used_labels = set()
            for item in download_links:
                lbl = label_for(item)
                unique = lbl
                dup = 2
                while unique in used_labels:
                    unique = f"{lbl}_{dup}"
                    dup += 1
                used_labels.add(unique)
                planned.append((unique, item.get("url")))

            total_links = max(1, len(planned))

            def download_one(pair):
                lbl, stem_url = pair
                dest = stems_dir / f"{lbl}.{STEM_EXT}"
                mvsep_download_file(stem_url, dest)
                return lbl, dest

            # En paralelo (igual que la pestana Separar): son cientos de MB y
            # bajarlos de a uno hacia que la barra pareciera congelada en 65%
            # durante varios minutos.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            socketio.emit("midi_progress", {
                "status": "descargando_stems",
                "percent": 65,
                "message": f"Descargando {total_links} pistas en paralelo..."
            })

            completed = 0
            with ThreadPoolExecutor(max_workers=min(total_links, 6)) as executor:
                futures = [executor.submit(download_one, p) for p in planned]
                for fut in as_completed(futures):
                    lbl, dest = fut.result()
                    local_stems[lbl] = dest
                    completed += 1
                    socketio.emit("midi_progress", {
                        "status": "descargando_stems",
                        "percent": 65 + int((completed / total_links) * 10),
                        "message": f"Pistas descargadas: {completed} de {total_links}..."
                    })

            # Step 4: Transcribe each stem to MIDI
            socketio.emit("midi_progress", {
                "status": "transcribiendo",
                "percent": 75,
                "message": "Transcribiendo notas y mapeando bateria General MIDI (Canal 10)..."
            })

            all_stems_notes = {}
            tracks_info = []

            COLOR_MAP = {
                "drums": "#ef4444", "bateria": "#ef4444", "kick": "#dc2626", "snare": "#f87171", "hihat": "#fca5a5",
                "bass": "#f59e0b", "bajo": "#f59e0b",
                "piano": "#3b82f6", "teclados": "#60a5fa", "keys": "#3b82f6",
                "guitar": "#10b981", "guitarra": "#10b981",
                "vocals": "#ec4899", "vocales": "#ec4899", "melody": "#a855f7", "lead": "#a855f7",
                "other": "#8b5cf6", "otro": "#8b5cf6"
            }
            ICON_MAP = {
                "drums": "🥁", "bateria": "🥁", "kick": "🥁", "snare": "🥁", "hihat": "🥁",
                "bass": "🎸", "bajo": "🎸",
                "piano": "🎹", "teclados": "🎹", "keys": "🎹",
                "guitar": "🎸", "guitarra": "🎸",
                "vocals": "🎤", "vocales": "🎤", "melody": "✨", "lead": "✨",
                "other": "🔊", "otro": "🔊"
            }

            total_stems_count = len(local_stems)
            for s_idx, (stem_label, stem_path) in enumerate(local_stems.items()):
                stem_pct = 75 + int((s_idx / max(1, total_stems_count)) * 15)
                socketio.emit("midi_progress", {
                    "status": "transcribiendo",
                    "percent": stem_pct,
                    "message": f"Transcribiendo pista {stem_label} a MIDI..."
                })

                is_drum_stem = any(k in stem_label.lower() for k in ["drum", "bateria", "kick", "snare", "hihat", "ride", "crash", "tom", "perc"])
                if is_drum_stem:
                    notes = midi_engine.transcribe_drums_to_midi(stem_path, bpm=active_bpm, stem_label=stem_label)
                else:
                    notes = midi_engine.transcribe_tonal_to_midi(stem_path, instrument_type=stem_label)

                all_stems_notes[stem_label] = notes

                single_midi_name = f"{stem_label}.mid"
                single_midi_path = midi_dir / single_midi_name
                midi_engine.save_single_track_midi(notes, single_midi_path, track_name=stem_label, bpm=active_bpm, is_drum=is_drum_stem)

                key_match = stem_label.lower()
                c_icon = "🎵"
                c_color = "#3b82f6"
                for k, icon_v in ICON_MAP.items():
                    if k in key_match:
                        c_icon = icon_v
                        break
                for k, color_v in COLOR_MAP.items():
                    if k in key_match:
                        c_color = color_v
                        break

                tracks_info.append({
                    "name": stem_label,
                    "icon": c_icon,
                    "color": c_color,
                    "note_count": len(notes),
                    "is_drum": is_drum_stem,
                    "channel": 10 if is_drum_stem else 1,
                    "audio_url": f"/download_midi/{project_folder.name}/stems/{stem_label}.{STEM_EXT}",
                    "midi_url": f"/download_midi/{project_folder.name}/midi_stems/{stem_label}.mid",
                    "midi_filename": f"{song_title} - {stem_label}.mid",
                    "notes": notes[:800]
                })

            # Step 5: Assemble Master Multi-Track MIDI File
            socketio.emit("midi_progress", {
                "status": "ensamblando",
                "percent": 92,
                "message": "Ensamblando Master MIDI Multitrack Type-1 compatible con DAWs..."
            })

            master_midi_name = f"{song_title} - Multitrack Master.mid"
            master_midi_path = project_folder / master_midi_name
            midi_engine.create_multitrack_midi(all_stems_notes, master_midi_path, bpm=active_bpm, song_title=song_title)

            # Step 6: Create ZIP Package
            socketio.emit("midi_progress", {
                "status": "empaquetando",
                "percent": 96,
                "message": "Empaquetando archivos MIDI y stems en ZIP..."
            })

            zip_filename = f"{song_title} - MIDI & Stems Pack.zip"
            zip_path = project_folder / zip_filename
            with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(str(master_midi_path), arcname=master_midi_name)
                for f in midi_dir.glob("*.mid"):
                    zf.write(str(f), arcname=f"MIDI_Separados/{f.name}")
                for f in stems_dir.glob(f"*.{STEM_EXT}"):
                    zf.write(str(f), arcname=f"Stems_Audio/{f.name}")

            # Complete!
            socketio.emit("midi_complete", {
                "folder": project_folder.name,
                "song_title": song_title,
                "bpm": active_bpm,
                "master_midi_url": f"/download_midi/{project_folder.name}/{master_midi_name}",
                "master_midi_name": master_midi_name,
                "zip_url": f"/download_midi_zip/{project_folder.name}",
                "zip_name": zip_filename,
                "tracks": tracks_info,
                "total_notes": sum(t["note_count"] for t in tracks_info)
            })

        except Exception as e:
            log(f"[MIDI] Error en pipeline: {e}\n{traceback.format_exc()}")
            socketio.emit("midi_error", {"error": f"Error inesperado en transcripcion MIDI: {str(e)[:200]}"})

    threading.Thread(target=run_pipeline, daemon=True).start()


@app.route("/download_midi/<path:filename>")
def download_midi_route(filename):
    file_path = MIDI_PROJECTS_DIR / filename
    if file_path.exists():
        ext = file_path.suffix.lower()
        mime = "audio/midi" if ext in (".mid", ".midi") else MIME_MAP.get(ext, "application/octet-stream")
        return send_file(file_path, as_attachment=True, download_name=file_path.name, mimetype=mime)
    return jsonify({"error": "Archivo no encontrado"}), 404


@app.route("/download_midi_zip/<path:folder>")
def download_midi_zip_route(folder):
    proj_dir = MIDI_PROJECTS_DIR / folder
    if proj_dir.exists():
        zip_files = list(proj_dir.glob("*.zip"))
        if zip_files:
            return send_file(zip_files[0], as_attachment=True, download_name=zip_files[0].name, mimetype="application/zip")
    return jsonify({"error": "Paquete ZIP no encontrado"}), 404


@app.route("/api/open_folder", methods=["POST"])
def api_open_folder():
    data = request.get_json() or {}
    rel_path = data.get("path", "")
    target = MIDI_PROJECTS_DIR / rel_path if rel_path else MIDI_PROJECTS_DIR
    try:
        if target.exists():
            if target.is_file():
                subprocess.Popen(f'explorer /select,"{str(target)}"')
            else:
                subprocess.Popen(f'explorer "{str(target)}"')
            return jsonify({"success": True})
        return jsonify({"error": "Carpeta no encontrada"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _cleanup_folder(folder):
    try:
        if folder.exists():
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
    except Exception:
        pass


if __name__ == "__main__":
    port = 5000
    url = f"http://127.0.0.1:{port}"

    print("=" * 50)
    print("  BPMSTART DOWNLOADER")
    print(f"  Abre {url} en tu navegador")
    print("=" * 50)

    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    socketio.run(app, host="0.0.0.0", port=port, debug=not getattr(sys, 'frozen', False), allow_unsafe_werkzeug=True)

