@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title 物流监控守护
echo ========================================
echo  物流双节奏守护（Edge 抓号 + 查轨迹邮件）
echo  目录: %CD%
echo ========================================
echo.

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

call "%~dp0..\_find_python.bat"
if errorlevel 1 (
  echo.
  echo 若你平时用「py -3」能跑，说明 bat 之前找错了 python。
  pause
  exit /b 1
)

REM 启动前自检 Edge（仅提示，不阻断查轨迹）
"!PYTHON_EXE!" -c "from src.logistics.edge_cdp import resolve_edge_binary; p=resolve_edge_binary(); print('Edge:', p)"
if errorlevel 1 (
  echo [警告] Edge 路径解析失败：今日抓号可能失败，但仍会用已有单号查轨迹。
  echo.
)

echo.
echo 启动 scripts\run_logistics_daemon.py ...
"!PYTHON_EXE!" scripts\run_logistics_daemon.py %*
set "EC=!ERRORLEVEL!"
echo.
if not "!EC!"=="0" (
  echo 进程异常退出 code=!EC!
) else (
  echo 守护已结束。
)
pause
exit /b !EC!
