@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 启动灾备监控（开发端待命，生产失联后自动接管）...
echo 工作目录: %cd%
python scripts\run_failover_standby.py
if errorlevel 1 (
  echo.
  echo 进程异常退出，请查看上方报错。
  pause
)
