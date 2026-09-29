@echo off
title BPMStartPRO Desktop
echo ========================================
echo   Iniciando BPMStartPRO Desktop...
echo ========================================
python desktop.py
if %errorlevel% neq 0 (
    echo.
    echo Ocurrio un error al ejecutar desktop.py
    pause
)
