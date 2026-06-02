@echo off
REM Daily morning routine - run by Task Scheduler at 08:45 on weekdays.
REM Targets the PM2 Windows Service. Restarts the apps for a fresh
REM Angel One login token (tokens expire daily). The service itself
REM keeps the daemon alive 24/7 - this just refreshes the apps.

set "PM2_HOME=C:\ProgramData\pm2\home"
set "PM2CMD=C:\ProgramData\npm\npm\pm2.cmd"
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

call "%PM2CMD%" start ecosystem.config.js
call "%PM2CMD%" restart all --update-env
call "%PM2CMD%" save

echo [%date% %time%] Morning routine executed >> logs\morning-restart.log
