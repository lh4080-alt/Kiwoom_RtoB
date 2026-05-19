# DEPRECATED — 2026-05-19
# daily_quality_logger를 봇 내부 task(automation/core/daily_task.py)로 통합하면서
# 이 외부 Task Scheduler 등록 스크립트는 사용하지 않음.
#
# 영구 원칙(메모리): 외부 프로세스에서 봇 데이터 파일 직접 조작 금지.
# 이 스크립트로 등록 시 daily_logger가 봇 데이터(collection_pool.json)를
# 외부 프로세스에서 비우게 되어 race condition 발생 (5/18, 5/19 사고).
#
# 향후 사용 금지. 참조용으로만 보관.
#
# ============== 이하 원본 (DO NOT RUN) ==============
#
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
