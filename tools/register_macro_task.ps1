# RtoB_Macro_Daily 등록 — 평일 15:50 거시 모니터 (macro_once.py daily)
# 등록: powershell -NoProfile -ExecutionPolicy Bypass -File tools\register_macro_task.ps1
$ErrorActionPreference = 'Stop'

$cmd = "cd /d C:\Kiwoom_RtoB\automation && python macro_once.py daily >> logs\macro_once.log 2>&1"

$Action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $cmd"
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 15:50
$Principal = New-ScheduledTaskPrincipal -UserId 'lh408' -LogonType Interactive

Register-ScheduledTask -TaskName 'RtoB_Macro_Daily' -Action $Action -Trigger $Trigger `
    -Principal $Principal -Description '거시 모니터: 섹터x거시 일별 관찰 리포트 (코스피 ETF 연구 P1)' -Force

Write-Host '등록 완료:'
(Get-ScheduledTask RtoB_Macro_Daily).State
(Get-ScheduledTask RtoB_Macro_Daily | Get-ScheduledTaskInfo).NextRunTime
