@echo off
cd /d %~dp0
python -m pip install requests
echo.
python get_chat_id.py
pause

