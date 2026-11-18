@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ========== 一键启动：普通订阅 + 大客户 VIP + 物流 ==========
cd /d "%~dp0"
title 上新全部守护（公共 + VIP + 物流）

if not exist "sub-push\app.py" (
  echo [错误] 找不到 sub-push\app.py
  pause
  exit /b 1
)
if not exist "big\scripts\run_logistics_daemon.py" (
  echo [错误] 找不到 big\scripts\run_logistics_daemon.py
  pause
  exit /b 1
)

echo ========================================
echo  一键启动
echo  - 普通订阅（公共 → 后端）
echo  - 大客户 VIP（AI + 邮件）
echo  - 物流守护（Edge 抓号 + 查轨迹邮件）
echo  目录: %CD%
echo ========================================
echo.

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

call "%~dp0_find_python.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo 启动 GUI（自动启动全部）...
"!PYTHON_EXE!" "%~dp0app.py" --auto-start
set "EC=!ERRORLEVEL!"

echo.
if not "!EC!"=="0" (
  echo 进程异常退出 code=!EC!
  pause
)
exit /b !EC!
