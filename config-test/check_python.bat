@echo off
echo ========================================
echo Python Installation Check
echo ========================================
echo.

python --version
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python and add it to PATH, then try again.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Python is installed correctly.
echo.
pause

