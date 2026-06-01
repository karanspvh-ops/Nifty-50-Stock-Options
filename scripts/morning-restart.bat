@echo off
REM Daily morning restart - forces a fresh Angel One login (tokens expire daily)
REM Run automatically by Task Scheduler at 08:45 on weekdays.

cd /d "C:\Users\karan\OneDrive\Desktop\Project\Algo Strategies\Nifty 50 Stock Options"

REM Make sure PM2 processes exist (in case they were not restored)
call pm2 resurrect

REM Restart the engine to get a brand-new Angel One session token
call pm2 restart trading-engine --update-env

echo [%date% %time%] Morning restart executed >> logs\morning-restart.log
