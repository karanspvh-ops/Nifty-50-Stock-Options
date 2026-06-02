@echo off
title Nifty 50 Trading Platform - Morning Start
color 0A

echo ============================================================
echo   NIFTY 50 OPTIONS TRADING PLATFORM
echo   Starting fresh session for today...
echo ============================================================
echo.

set "PM2=C:\Users\karan\AppData\Roaming\npm\pm2.cmd"
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

echo [1/4] Starting services (from config if not running)...
call "%PM2%" start ecosystem.config.js

echo [2/4] Restarting for a fresh Angel One login...
call "%PM2%" restart all --update-env
call "%PM2%" save

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
