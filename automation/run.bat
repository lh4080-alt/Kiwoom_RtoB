@echo off
cd /d "%~dp0"
python -m pip install requests websockets pandas httpx
echo.
python core/run_wrapper.py
pause

