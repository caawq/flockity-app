set PYTHONIOENCODING=utf-8
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

REM Setup virtual environment (one-time)
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment with Python 3.9...
    py -3.9 -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install dependencies (one-time, use pre-built wheels to avoid compilation)
pip install --only-binary :all: -r requirements.txt --quiet --no-cache-dir 2>nul
if errorlevel 1 (
    echo [SETUP] Installing with fallback...
    pip install -r requirements.txt --quiet --no-cache-dir
)

REM Launch orchestrator with venv Python
echo [LAUNCH] Starting orchestrator...
python start.py

REM Cleanup on exit
echo [SHUTDOWN] Cleaning up...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq FLOCKITY*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
pause
