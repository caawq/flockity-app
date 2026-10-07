@echo off
REM =========================================
REM Flockity Production Launcher
REM =========================================
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title FLOCKITY Launch Manager

REM Kill orphan processes from previous runs
echo [CLEANUP] Terminating orphan processes...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq FLOCKITY*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
taskkill /F /IM FL64.exe >nul 2>&1

REM Launch orchestrator directly with system Python
echo [LAUNCH] Starting orchestrator...
python start.py

REM Cleanup on exit
echo [SHUTDOWN] Cleaning up...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq FLOCKITY*" >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
pause