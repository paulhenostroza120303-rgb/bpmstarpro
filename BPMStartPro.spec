# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('bin', 'bin'), ('templates', 'templates'), ('static', 'static'), ('app.py', '.'), ('binaries.py', '.'), ('midi_engine.py', '.')]
binaries = []
hiddenimports = ['mido.backends.rtmidi', 'scipy.special', 'webview', 'webview.platforms', 'webview.platforms.edgechromium', 'engineio.async_drivers.threading', 'flask_socketio', 'engineio', 'socketio', 'werkzeug.serving', 'pythonnet', 'clr', 'proxy_tools', 'bottle']
hiddenimports += collect_submodules('numba')
hiddenimports += collect_submodules('llvmlite')
tmp_ret = collect_all('basic_pitch')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('librosa')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('soundfile')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('lazy_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['eventlet', 'gevent', 'PyQt5', 'PyQtWebEngine', 'torch', 'torchaudio', 'torchvision', 'transformers', 'tensorflow', 'faiss', 'av', 'matplotlib', 'PIL', 'sklearn', 'IPython', 'notebook', 'pytest'],
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
