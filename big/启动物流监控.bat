@echo off
chcp 65001 >nul
cd /d "%~dp0"

title 物流监控（建议用 GUI）
echo 提示：物流已集成到「启动big专供推送.bat」GUI 中。
echo 本 bat 仍可单独启动命令行物流守护。
echo.

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python scripts\run_logistics_daemon.py
) else (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 scripts\run_logistics_daemon.py
  ) else (
    echo [错误] 未找到 python
    pause
    exit /b 1
  )
)

echo.
pause
