@echo off
chcp 65001 > nul
title kiwoom_RtoB Errors Only
echo === Errors / Exceptions Only ===
echo Press Ctrl+C to stop
echo.
ssh beelink "powershell -Command \"Get-Content C:\Kiwoom_RtoB\logs\bot.log -Wait -Tail 100 -Encoding UTF8 | Where-Object { $_ -match 'ERROR|Exception|Traceback|CRITICAL|failed' }\""
pause
