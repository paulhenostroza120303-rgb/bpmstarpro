# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('bin', 'bin'), ('templates', 'templates'), ('static', 'static'), ('app.py', '.'), ('binaries.py', '.')],
    hiddenimports=['webview', 'webview.platforms', 'webview.platforms.edgechromium', 'engineio.async_drivers.threading', 'flask_socketio', 'engineio', 'socketio', 'werkzeug.serving', 'pythonnet', 'clr', 'proxy_tools', 'bottle'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['eventlet', 'gevent', 'PyQt5', 'PyQtWebEngine'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BPMStartPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BPMStartPro',
)
