@echo off
title Nifty 50 Trading Platform - Morning Start
color 0A

echo ============================================================
echo   NIFTY 50 OPTIONS TRADING PLATFORM
echo   Starting fresh session for today...
echo ============================================================
echo.

cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

echo [1/4] Ensuring services are running...
call pm2 resurrect >nul 2>&1

echo [2/4] Restarting engine for a fresh Angel One login...
call pm2 restart all --update-env >nul 2>&1

echo [3/4] Waiting for login + market feed (15s)...
timeout /t 15 /nobreak >nul

echo [4/4] Opening dashboard...
start http://localhost:3000

echo.
echo ============================================================
echo   DONE! Dashboard opening in your browser.
echo.
echo   Check the feed dot is GREEN at the top of the dashboard.
echo   The scanner auto-starts at 9:00 AM.
echo ============================================================
echo.
echo   This window will close in 8 seconds...
timeout /t 8 /nobreak >nul
