@echo off
title Nifty 50 Trading Platform - Open Dashboard
color 0A

echo ============================================================
echo   NIFTY 50 OPTIONS TRADING PLATFORM
echo ============================================================
echo.
echo   The platform runs 24/7 as a Windows Service, so it should
echo   already be up. This will make sure, then open the dashboard.
echo.

set "PM2_HOME=C:\ProgramData\pm2\home"
set "PM2CMD=C:\ProgramData\npm\npm\pm2.cmd"
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

echo Ensuring apps are running...
call "%PM2CMD%" start ecosystem.config.js >nul 2>&1

echo Opening dashboard...
start http://localhost:3000

echo.
echo   Done. Check the feed dot is GREEN at the top.
timeout /t 6 /nobreak >nul
