@echo off
title Nifty 50 Trading Platform
color 0A
set "PROJ=C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"
set "SMTP_USER=spventures.inv@gmail.com"
set "SMTP_PASS=bwutzmkwhrwxbcrz"
set "PYTHON=C:\Users\karan\AppData\Local\Python\bin\python3.14.exe"

echo ============================================================
echo   NIFTY 50 OPTIONS TRADING PLATFORM
echo ============================================================
echo.

REM --- If backend is already running, just open the dashboard ---
powershell -NoProfile -Command "try{Invoke-WebRequest 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 (
  echo Backend already running. Checking frontend...
  powershell -NoProfile -Command "try{Invoke-WebRequest 'http://localhost:3000' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0}catch{exit 1}"
  if %errorlevel%==0 (
    echo Frontend also running. Opening dashboard...
    start http://localhost:3000
    timeout /t 3 /nobreak >nul
    exit /b 0
  )
  echo Frontend not running. Starting it...
  goto start_frontend
)

echo [1/3] Starting backend engine...
start "Trading Backend" cmd /k "cd /d "%PROJ%" && set PYTHONPATH=%PROJ% && "%PYTHON%" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

:start_frontend
echo [2/3] Starting frontend dashboard...
start "Trading Frontend" cmd /k "cd /d "%PROJ%\frontend" && npm run dev -- --port 3000"

echo [3/3] Waiting for startup (12s)...
timeout /t 12 /nobreak >nul

echo Opening dashboard...
start http://localhost:3000

echo.
echo ============================================================
echo   DONE - Two terminal windows are now open:
echo     - "Trading Backend"  (port 8000) — DO NOT CLOSE
echo     - "Trading Frontend" (port 3000) — safe to close
echo   Check the feed dot is GREEN at the top of the dashboard.
echo ============================================================
timeout /t 6 /nobreak >nul
