import os
import sys
from pathlib import Path


def get_bundle_root():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BUNDLE_ROOT = get_bundle_root()
BIN_DIR = BUNDLE_ROOT / "bin"


def get_ytdlp_path():
    ytdlp = BIN_DIR / "yt-dlp.exe"
    if ytdlp.exists():
        return str(ytdlp)
    import shutil
    system_ytdlp = shutil.which("yt-dlp")
    if system_ytdlp:
        return system_ytdlp
    raise FileNotFoundError("yt-dlp.exe no encontrado en bin/ ni en el sistema.")


def get_js_runtime_arg():
    bundled = BIN_DIR / "node.exe"
    if bundled.exists():
        return ["--js-runtimes", "node:%s" % bundled]
    import shutil
    node = shutil.which("node")
    if not node:
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "node.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "nodejs", "node.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe"),
        ]
        for path in candidates:
            if path and os.path.exists(path):
                node = path
                break
    if node:
        return ["--js-runtimes", "node:%s" % node]
    return []


def get_ffmpeg_path():
    ffmpeg = BIN_DIR / "ffmpeg.exe"
    if ffmpeg.exists():
        return str(ffmpeg)
    import shutil
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    raise FileNotFoundError("ffmpeg.exe no encontrado en bin/ ni en el sistema.")


def get_ffmpeg_dir():
    return str(Path(get_ffmpeg_path()).parent)


def reset_dll_path():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        except Exception:
            pass
