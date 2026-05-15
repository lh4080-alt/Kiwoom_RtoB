@echo off
cd /d %~dp0
echo ========================================
echo Telegram Configuration Check and Test
echo ========================================
echo.

REM Check if requests module is installed, install if not
python -c "import requests" 2>nul
if %errorlevel% neq 0 (
    echo requests module is not installed. Installing...
    python -m pip install requests
    echo.
)

echo Checking telegram settings in config folder and sending test message.
echo.
python test_telegram.py
echo.
pause

