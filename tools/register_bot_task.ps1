# ============================================
# kiwoom_RtoB 메인 봇 Task Scheduler 등록
#
# 가동 방식: 데몬 (한 번 띄우면 무한 루프, 장 시간 자동 처리)
# 엔트리: automation/core/run_wrapper.py
# stdout/stderr → C:\Kiwoom_RtoB\logs\bot.log (append)
#
# 트리거:
#   - 부팅 시 (전원 복귀/재시작 후 자동 가동)
#   - 매일 08:30 (장 시작 30분 전, 안전망)
#
# 다중 실행 방지: MultipleInstances=IgnoreNew (이미 돌면 새로 안 띄움)
# 자동 재시작: 프로세스 종료 시 1분 후 재시도, 최대 3회
# ============================================

$TaskName = "KiwoomRtoB_Bot"
$ProjectRoot = "C:\Kiwoom_RtoB"
$LogFile = "$ProjectRoot\logs\bot.log"
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
$BotEntry = "core/run_wrapper.py"
$WorkingDir = "$ProjectRoot\automation"

# 기존 동명 task 제거 (재등록 안전)
schtasks /Delete /TN $TaskName /F 2>$null

# cmd 래퍼로 stdout/stderr 리다이렉트
# python -u: unbuffered stdout (redirect 시 block-buffering으로 로그 안 쌓이는 문제 방지)
$cmdArg = "/c `"`"$PythonExe`" -u $BotEntry >> `"$LogFile`" 2>&1`""

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument $cmdArg `
    -WorkingDirectory $WorkingDir

# 트리거 2개: 부팅 + 매일 08:30
$trigger1 = New-ScheduledTaskTrigger -AtStartup
$trigger2 = New-ScheduledTaskTrigger -Daily -At "08:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger1, $trigger2) `
    -Settings $settings `
    -RunLevel Highest `
    -User "lh408" `
    -Description "kiwoom_RtoB main bot (daemon — boot + daily 08:30, restart on failure)"

Write-Host "Task '$TaskName' registered."
schtasks /Query /TN $TaskName /FO LIST /V
