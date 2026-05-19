@echo off
chcp 65001 > nul
title kiwoom_RtoB Daily Task
echo === Daily Task (16:30) Activity ===
echo Press Ctrl+C to stop
echo.
ssh beelink "powershell -Command \"Get-Content C:\Kiwoom_RtoB\logs\bot.log -Wait -Tail 100 -Encoding UTF8 | Where-Object { $_ -match 'daily|evaluate|backfill|rebuild_master|clear_pool|DailyTaskManager|startup' }\""
pause
