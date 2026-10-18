@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 installer\install.py
    goto end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python installer\install.py
    goto end
)

echo.
echo   未找到 Python 3.10+，请先安装：https://www.python.org/downloads/
echo.
pause

:end
