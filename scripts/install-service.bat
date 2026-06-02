@echo off
REM =====================================================================
REM  ONE-TIME SETUP - RIGHT-CLICK THIS FILE > "Run as administrator"
REM  Converts PM2 into a true Windows Service so the trading platform
REM  runs at OS level and survives ANY window/terminal/logout.
REM =====================================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo  ERROR: Not running as Administrator.
  echo  Right-click this file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo [1/5] Stopping the old window-tied PM2 (frees ports 8000/3000)...
call "C:\Users\karan\AppData\Roaming\npm\pm2.cmd" kill >nul 2>&1

echo [2/5] Configuring system (npm prefix + PM2_HOME at OS level)...
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options\tools\pm2-installer"
call npm run configure

echo [3/5] Installing the PM2 Windows Service...
call npm run setup

echo [4/5] Registering the trading apps with the service...
set "PM2_HOME=C:\ProgramData\pm2\home"
set "PM2CMD=C:\ProgramData\npm\npm\pm2.cmd"
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"
call "%PM2CMD%" start ecosystem.config.js
call "%PM2CMD%" save

echo [5/5] Done.
echo.
echo =====================================================================
echo   SUCCESS - The platform now runs as a Windows Service.
echo   It will auto-start on boot and survive any window closing.
echo   Open http://localhost:3000 to confirm.
echo =====================================================================
echo.
pause
