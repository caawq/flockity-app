@echo off
REM =========================================
REM Flockity Production Launcher
REM =========================================
chcp 65001 >nul
title FLOCKITY Launch Manager

REM Kill orphan processes from previous runs
echo [CLEANUP] Terminating orphan processes...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq FLOCKITY*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1

REM Activate virtual environment
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Creating...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install --upgrade pip
    pip install -r requirements.txt --no-cache-dir
) else (
    call venv\Scripts\activate.bat
)

REM Verify dependencies
echo [CHECK] Verifying dependencies...
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [ERROR] Dependencies missing. Installing...
    pip install -r requirements.txt --no-cache-dir
)

REM Launch orchestrator
echo [LAUNCH] Starting orchestrator...
python start.py

REM Cleanup on exit
echo [SHUTDOWN] Cleaning up...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq FLOCKITY*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
pause
