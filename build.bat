@echo off
echo ========================================
echo   BPMSTART DOWNLOADER - Build Windows
echo ========================================
echo.

if not exist "bin\yt-dlp.exe" (
    echo [!] Descargando yt-dlp.exe...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile 'bin\yt-dlp.exe'"
)

if not exist "bin\ffmpeg.exe" (
    where ffmpeg >nul 2>&1
    if %errorlevel% neq 0 (
        echo [!] ffmpeg.exe no encontrado en bin\ ni en el sistema
        echo     Descarga ffmpeg de: https://www.gyan.dev/ffmpeg/builds/
        echo     Extrae ffmpeg.exe a la carpeta bin\
        echo.
        pause
        exit /b 1
    ) else (
        echo [OK] ffmpeg encontrado en el sistema
    )
)

echo [OK] Binarios listos.
echo.
echo Compilando BPMStartPro (esto tarda 3-5 min)...
echo.

pyinstaller --clean --onedir --noconsole ^
    --add-data "bin;bin" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "app.py;." ^
    --add-data "binaries.py;." ^
    --hidden-import webview ^
    --hidden-import webview.platforms ^
    --hidden-import webview.platforms.edgechromium ^
    --hidden-import engineio.async_drivers.threading ^
    --hidden-import flask_socketio ^
    --hidden-import engineio ^
    --hidden-import socketio ^
    --hidden-import werkzeug.serving ^
    --hidden-import pythonnet ^
    --hidden-import clr ^
    --hidden-import proxy_tools ^
    --hidden-import bottle ^
    --exclude-module eventlet ^
    --exclude-module gevent ^
    --exclude-module PyQt5 ^
    --exclude-module PyQtWebEngine ^
    --name "BPMStartPro" ^
    desktop.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Fallo la compilacion.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   BUILD COMPLETADO
echo   Exe en: dist\BPMStartPro\BPMStartPro.exe
echo ========================================
echo.
pause
