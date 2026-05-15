@echo off
cd /d %~dp0
python -m pip install requests
echo.
echo ========================================
echo Paper Trading Login Test
echo ========================================
python login.py paper
echo.
echo ========================================
echo Real Trading Login Test
echo ========================================
python login.py real
echo.
pause

