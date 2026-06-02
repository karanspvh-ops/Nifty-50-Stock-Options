@echo off
REM =====================================================================
REM  RIGHT-CLICK THIS FILE > "Run as administrator"
REM  Removes the LocalSystem PM2 service that could not run the apps
REM  (your Python is user-installed and the project lives in OneDrive,
REM  both inaccessible to the SYSTEM account). After this, the platform
REM  runs cleanly as you via the desktop button.
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

echo Removing the PM2 Windows service...
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options\tools\pm2-installer"
call npm run remove

echo.
echo ============================================================
echo   Done. The broken service has been removed.
echo   Use the desktop button "Start Trading Platform" to run it.
echo ============================================================
echo.
pause
