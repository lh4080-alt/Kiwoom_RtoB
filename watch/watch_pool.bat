@echo off
chcp 65001 > nul
title kiwoom_RtoB Pool Activity
echo === Pool / Buy Activity (1차/2차 풀, 매수) ===
echo Press Ctrl+C to stop
echo.
ssh beelink "powershell -Command \"Get-Content C:\Kiwoom_RtoB\logs\bot.log -Wait -Tail 100 -Encoding UTF8 | Where-Object { $_ -match 'primary|secondary|BUY:|d_score|pool entered|pool passed|pool violated|수집풀|daily buy' }\""
pause
