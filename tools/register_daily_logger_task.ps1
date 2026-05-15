# KiwoomRtoB_DailyQualityLogger Task Scheduler 등록 스크립트
#
# 실행 전제:
#   1. Beelink 머신에 C:\kiwoom_RtoB 클론 완료
#   2. .venv 가상환경 생성 + pip install 완료
#   3. PowerShell을 **Administrator 권한**으로 실행 (필수)
#
# 사용법:
#   PowerShell (관리자) → cd C:\kiwoom_RtoB\tools → .\register_daily_logger_task.ps1

$action = New-ScheduledTaskAction `
    -Execute "C:\kiwoom_RtoB\.venv\Scripts\python.exe" `
    -Argument "C:\kiwoom_RtoB\tools\daily_quality_logger.py" `
    -WorkingDirectory "C:\kiwoom_RtoB"

$trigger = New-ScheduledTaskTrigger -Daily -At "16:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "KiwoomRtoB_DailyQualityLogger" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Daily candle quality D-score logger"
