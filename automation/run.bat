@echo off
chcp 65001 >nul
REM ============================================================
REM [사용 중단] 이 run.bat은 파이썬을 직접 실행해 봇을 띄웠습니다.
REM 스케줄러 작업(KiwoomRtoB_Bot)과 동시에 뜨면 single-instance 가드가
REM 서로 taskkill 하여 봇이 죽습니다 (2026-06-12 사고). 직접 실행 금지.
REM
REM 봇 기동은 start_bot.bat 을 사용하세요 (작업 트리거 = 단일 인스턴스, 로그·자동복구).
REM 의존성 설치가 필요하면 수동으로:
REM   .venv\Scripts\python.exe -m pip install requests websockets pandas httpx
REM ============================================================
echo [run.bat 사용 중단] 봇은 start_bot.bat 으로 띄우세요 (단일 인스턴스 보장).
echo start_bot.bat 으로 전환합니다...
echo.
cd /d "%~dp0"
call start_bot.bat
