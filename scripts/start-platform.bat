@echo off
title Nifty 50 Trading Platform
color 0A
set "PROJ=C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

echo ============================================================
echo   NIFTY 50 OPTIONS TRADING PLATFORM
echo ============================================================
echo.

REM --- If it's already running, just open the dashboard ---
powershell -NoProfile -Command "try{Invoke-WebRequest 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 (
  echo Platform already running. Opening dashboard...
  start http://localhost:3000
  timeout /t 3 /nobreak >nul
  exit /b 0
)

echo [1/3] Starting backend engine...
powershell -NoProfile -Command "$env:PYTHONPATH='%PROJ%'; Start-Process python -ArgumentList '-m','uvicorn','backend.main:app','--host','0.0.0.0','--port','8000' -WorkingDirectory '%PROJ%' -WindowStyle Hidden"

echo [2/3] Starting dashboard server...
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c','npm run preview -- --port 3000 --host' -WorkingDirectory '%PROJ%\frontend' -WindowStyle Hidden"

echo [3/3] Waiting for startup (15s)...
timeout /t 15 /nobreak >nul

echo Opening dashboard...
start http://localhost:3000

echo.
echo ============================================================
echo   DONE - check the feed dot is GREEN at the top.
echo   These run in the background; closing THIS window is fine.
echo ============================================================
timeout /t 6 /nobreak >nul
