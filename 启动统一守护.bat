@echo off
chcp 65001 >nul
cd /d "%~dp0sub-push"
if not exist "app.py" (
  echo [错误] 找不到统一项目: %cd%\app.py
  pause
  exit /b 1
)
echo 启动统一守护（公共后端 + VIP）...
echo 工作目录: %cd%
python app.py
if errorlevel 1 (
  echo.
  echo 进程异常退出，请查看上方报错。
  pause
)
