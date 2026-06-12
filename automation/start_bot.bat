@echo off
chcp 65001 >nul
REM ============================================================
REM kiwoom_RtoB 봇 기동 — 스케줄러 작업(KiwoomRtoB_Bot) 트리거 전용.
REM
REM 파이썬을 직접 실행하지 않고 등록된 작업을 띄웁니다. 이유:
REM   - 단일 인스턴스 보장 (run.bat 직접 실행과 동시에 뜨면 single-instance
REM     가드가 서로 taskkill → 봇 사망. 2026-06-12 사고.)
REM   - S4U 작업이라 로그인 없이도 부팅/08:30 자동 기동·실패 시 재시작.
REM   - stdout이 C:\Kiwoom_RtoB\logs\bot.log 로 기록됨 (run.bat 직접 실행은 콘솔만).
REM ============================================================
echo [start_bot] KiwoomRtoB_Bot 작업을 기동합니다...
schtasks /Run /TN KiwoomRtoB_Bot
if errorlevel 1 (
    echo [start_bot] 기동 실패 — 작업이 등록돼 있는지 확인하세요.
    echo            관리자 PowerShell에서 tools\register_bot_task.ps1 재실행.
) else (
    echo [start_bot] 기동 요청 완료. 로그: C:\Kiwoom_RtoB\logs\bot.log
    echo            확인: powershell "Get-Content C:\Kiwoom_RtoB\logs\bot.log -Tail 15"
)
pause
