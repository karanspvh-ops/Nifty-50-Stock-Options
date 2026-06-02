@echo off
REM Daily morning routine - run by Task Scheduler at 08:45 on weekdays.
REM Robust against cold boot: starts from config if not running, then
REM restarts for a fresh Angel One session (tokens expire daily).

set "PM2=C:\Users\karan\AppData\Roaming\npm\pm2.cmd"
cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

REM Start both apps from config (no-op if already running)
call "%PM2%" start ecosystem.config.js

REM Restart for a brand-new Angel One login token
call "%PM2%" restart all --update-env

call "%PM2%" save

echo [%date% %time%] Morning routine executed >> logs\morning-restart.log
