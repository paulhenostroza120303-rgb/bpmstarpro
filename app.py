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

APP_VERSION = "1.0.1"
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

AUTH_FILE = BASE_DIR / ".bpmstart_auth"

active_downloads = {}
active_separations = {}

YT_CLIENTS = ["default", "android", "tv", "web_embedded", "ios"]
_working_client = "default"
_working_cookies = []


def client_arg(client):
    if client == "default":
        return []
    return ["--extractor-args", "youtube:player_client=%s" % client]


def cookies_arg():
    c = BASE_DIR / "cookies.txt"
    if c.exists():
        return ["--cookies", str(c)]
    return []


def cookies_from_browser_arg():
    local = os.environ.get("LOCALAPPDATA", "")
    apdata = os.environ.get("APPDATA", "")
    firefox_profiles = os.path.join(apdata, "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(firefox_profiles):
        try:
            for entry in os.scandir(firefox_profiles):
                if entry.is_dir() and os.path.exists(os.path.join(entry.path, "cookies.sqlite")):
                    return ["--cookies-from-browser", "firefox"]
        except Exception:
            pass
    profiles = [
        ("edge", os.path.join(local, "Microsoft", "Edge", "User Data", "Default", "Cookies")),
        ("chrome", os.path.join(local, "Google", "Chrome", "User Data", "Default", "Cookies")),
        ("brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cookies")),
        ("opera", os.path.join(apdata, "Opera Software", "Opera Stable", "Cookies")),
    ]
    for name, path in profiles:
        if os.path.exists(path):
            return ["--cookies-from-browser", name]
    return []


def all_cookies_arg():
    explicit = cookies_arg()
    if explicit:
        return explicit
    return cookies_from_browser_arg()


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

# ========================
#  LOGIN (file-based)
# ========================

LOGIN_USER = "bpmstartpro"
LOGIN_PASS = "bpmstart.vercel.app"


def is_logged_in():
    return AUTH_FILE.exists()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


@app.route("/api/check_update")
@login_required
def api_check_update():
    update_info = check_for_updates()
    if update_info:
        return jsonify(update_info)
    return jsonify({"has_update": False, "current_version": APP_VERSION})


@app.route("/api/trigger_update", methods=["POST"])
@login_required
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


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if is_logged_in():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == LOGIN_USER and password == LOGIN_PASS:
            AUTH_FILE.write_text(f"{username}", encoding="utf-8")
            return redirect(url_for("index"))
        else:
            error = "Usuario o contrasena incorrectos"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()
    return redirect(url_for("login_page"))


# ========================
#  MAIN APP
# ========================

KEY_FILE = BASE_DIR / ".bpmstart_key"


def get_user_key():
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return None


def save_user_key(key):
    KEY_FILE.write_text(key.strip(), encoding="utf-8")


def delete_user_key():
    if KEY_FILE.exists():
        KEY_FILE.unlink()


@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    key = get_user_key()
    return jsonify({"has_key": bool(key), "key": ("*" * len(key) if key else "")})


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings_save():
    data = request.get_json()
    codigo = data.get("codigo", "").strip()
    if not codigo:
        return jsonify({"success": False, "error": "El codigo no puede estar vacio"})
    if len(codigo) < 20:
        return jsonify({"success": False, "error": "El codigo parece demasiado corto"})
    save_user_key(codigo)
    return jsonify({"success": True})


@app.route("/api/settings/delete", methods=["POST"])
@login_required
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


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@socketio.on("analyze")
@login_required
def handle_analyze(data):
    url = data.get("url", "").strip()
    if not url:
        emit("analyze_error", {"error": "Por favor ingresa una URL valida."})
        return

    emit("analyzing", {"status": "Obteniendo informacion del video..."})

    try:
        global _working_client, _working_cookies
        ytdlp_path = get_ytdlp_path()
        js_runtime = get_js_runtime_arg()
        info = None
        last_err = ""

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
                last_err = result.stderr.strip()[:200]
                log("Client %s %s fallo: %s" % (client, tag, last_err))

            if info:
                break

        if not info:
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
@login_required
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
    output_template = str(DOWNLOADS_DIR / f"{safe_title}.%(ext)s")

    ytdlp_path = get_ytdlp_path()
    ffmpeg_dir = get_ffmpeg_dir()
    download_cookies = _working_cookies if _working_cookies else all_cookies_arg()
    cmd_base = [ytdlp_path, "--no-playlist", "-o", output_template, "--ffmpeg-location", ffmpeg_dir] + get_js_runtime_arg() + download_cookies

    if fmt == "mp3":
        cmd_base += ["-x", "--audio-format", "mp3", "--audio-quality",
                 "320K" if quality == "best" else "192K" if quality == "medium" else "128K"]
    elif fmt == "mp4":
        if quality == "best":
            cmd_base += ["-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"]
        elif quality == "medium":
            cmd_base += ["-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b"]
        else:
            cmd_base += ["-f", "bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]/b"]
        cmd_base += ["--merge-output-format", "mp4"]

    active_downloads[download_id] = {"file": None, "title": safe_title}

    emit("download_started", {"download_id": download_id})

    def run_download():
        try:
            global _working_client
            clients = [_working_client] + [c for c in YT_CLIENTS if c != _working_client] if _working_client in YT_CLIENTS else list(YT_CLIENTS)
            last_err = "Error durante la descarga. Verifica la URL."

            for client in clients:
                cmd = cmd_base + client_arg(client) + [url]
                log("Descargando %s | client: %s | cookies: %s" % (url, client, "si" if download_cookies else "no"))
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
                    
                    # 1. First look for exact title match with expected format extension
                    for f in DOWNLOADS_DIR.iterdir():
                        if f.is_file() and f.stem == safe_title and f.suffix.lower() == target_ext:
                            downloaded_file = f.name
                            break

                    # 2. Fallback: match title with any extension
                    if not downloaded_file:
                        for f in DOWNLOADS_DIR.iterdir():
                            if f.is_file() and f.stem == safe_title:
                                downloaded_file = f.name
                                break

                    # 3. Fallback: get most recently modified file in DOWNLOADS_DIR
                    if not downloaded_file:
                        files = [f for f in DOWNLOADS_DIR.iterdir() if f.is_file()]
                        if files:
                            files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                            downloaded_file = files[0].name

                    if downloaded_file:
                        ext = Path(downloaded_file).suffix.lstrip(".")
                        socketio.emit("download_complete", {
                            "download_id": download_id,
                            "filename": downloaded_file,
                            "title": safe_title,
                            "ext": ext,
                        })
                    else:
                        socketio.emit("download_error", {
                            "download_id": download_id,
                            "error": "Archivo no encontrado tras la descarga.",
                        })
                    return

                full_out = "\n".join(output_lines[-40:])
                last_err = full_out if full_out.strip() else last_err
                log("Descarga client %s fallo: %s" % (client, last_err[-200:]))

            err_msg = last_err[-200:] if last_err else "Error durante la descarga. Verifica la URL."
            if is_blocked(last_err):
                err_msg += " (YouTube esta limitando descargas directas. Intenta con cookies o reinicia la aplicacion)."

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
@login_required
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

def bpmstart_create_separation(file_path, sep_type=40, output_format=1, add_opt1=None, add_opt2=None, add_opt3=None, text_prompt=None):
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

        with open(send_path, "rb") as f:
            files = {"audiofile": f}
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

            try:
                resp = requests.post(url, files=files, data=data, timeout=600)
            except requests.exceptions.Timeout:
                return False, "Tiempo de espera agotado al subir a BpmStart Pro. Intenta de nuevo."
            except requests.exceptions.RequestException as e:
                return False, f"Error de conexion con BpmStart Pro: {str(e)[:150]}"

        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text[:300]
            except Exception:
                pass
            return False, f"BpmStart Pro respondio HTTP {resp.status_code}: {body}"

        try:
            result = resp.json()
        except Exception:
            return False, f"Respuesta inesperada del servidor (HTTP {resp.status_code})"

        if result.get("success"):
            return True, result["data"]["hash"]
        else:
            errors = result.get("errors")
            if isinstance(errors, list) and errors:
                msg = errors[0]
            elif isinstance(errors, str):
                msg = errors
            else:
                msg = result.get("data", {}).get("message") or result.get("message") or result.get("error") or "Error desconocido"
            return False, msg
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
@login_required
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
@login_required
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
                part_prefix = f"Parte {chunk_idx + 1}/{num_chunks}: " if num_chunks > 1 else ""
                base_pct = int((chunk_idx / num_chunks) * 75)
                chunk_slice_pct = int(75 / num_chunks)

                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "status": "subiendo",
                    "message": f"{part_prefix}Subiendo archivo a BpmStart Pro...",
                    "percent": max(5, base_pct + int(chunk_slice_pct * 0.1)),
                })

                success, result = bpmstart_create_separation(
                    chunk_info["path"],
                    sep_type=sep_type,
                    output_format=output_format,
                    add_opt1=add_opt1,
                    add_opt2=add_opt2,
                    add_opt3=add_opt3,
                    text_prompt=text_prompt,
                )

                if not success:
                    socketio.emit("sep_error", {
                        "sep_id": sep_id,
                        "error": f"Error al crear tarea ({part_prefix.strip()}): {result}",
                    })
                    return

                task_hash = result
                active_separations[sep_id]["hash"] = task_hash

                socketio.emit("sep_progress", {
                    "sep_id": sep_id,
                    "status": "procesando",
                    "message": f"{part_prefix}Procesando con BpmStart Pro...",
                    "percent": base_pct + int(chunk_slice_pct * 0.3),
                })

                max_wait = 1800
                elapsed = 0
                poll_interval = 10
                chunk_files_data = None

                while elapsed < max_wait:
                    time.sleep(poll_interval)
                    elapsed += poll_interval

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

                    if status in ("waiting", "processing", "distributing", "merging"):
                        sub_pct = 0.4
                        if status == "distributing":
                            finished = status_resp.get("data", {}).get("finished_chunks", 0)
                            total = status_resp.get("data", {}).get("all_chunks", 1)
                            sub_pct = 0.3 + (0.4 * finished / total) if total else 0.4
                        elif status == "merging":
                            sub_pct = 0.8

                        socketio.emit("sep_progress", {
                            "sep_id": sep_id,
                            "status": "procesando",
                            "message": f"{part_prefix}Procesando con BpmStart Pro...",
                            "percent": base_pct + int(chunk_slice_pct * sub_pct),
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
                    "status": "descargando",
                    "message": f"{part_prefix}Descargando {len(chunk_files_data)} pistas en paralelo...",
                    "percent": base_pct + int(chunk_slice_pct * 0.85),
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
                    "status": "procesando",
                    "message": "Fusión perfecta de pistas de audio (Crossfade)...",
                    "percent": 85,
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


@app.route("/upload_audio", methods=["POST"])
@login_required
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
@login_required
def download_stem(filename):
    file_path = SEPARATIONS_DIR / filename
    if file_path.exists():
        ext = file_path.suffix.lower()
        mime = MIME_MAP.get(ext, "application/octet-stream")
        return send_file(file_path, as_attachment=True,
                         download_name=file_path.name,
                         mimetype=mime)
    return jsonify({"error": "Stem no encontrado"}), 404


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
