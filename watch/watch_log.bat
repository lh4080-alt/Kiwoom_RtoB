@echo off
chcp 65001 > nul
title kiwoom_RtoB Bot Log
echo === kiwoom_RtoB Bot Log (Live Tail) ===
echo Press Ctrl+C to stop
echo.
ssh beelink "powershell -Command \"Get-Content C:\Kiwoom_RtoB\logs\bot.log -Wait -Tail 50 -Encoding UTF8\""
pause
