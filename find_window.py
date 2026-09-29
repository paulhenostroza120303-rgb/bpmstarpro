import ctypes
import ctypes.wintypes as w

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
results = []

def callback(hwnd, lparam):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    t = buf.value
    vis = bool(user32.IsWindowVisible(hwnd))
    if t:
        results.append((hwnd, vis, t))
    return True

user32.EnumWindows(WNDENUMPROC(callback), 0)

found = False
for h, v, t in results:
    if 'bpm' in t.lower() or 'downloader' in t.lower() or 'pywebview' in t.lower():
        print(f"ENCONTRADA: hwnd={h} visible={v} title='{t}'")
        found = True

if not found:
    print("NO se encontro ninguna ventana BPMSTART")
    print("Ventanas visibles activas:")
    for h, v, t in results:
        if v:
            print(f"  - '{t}'")
