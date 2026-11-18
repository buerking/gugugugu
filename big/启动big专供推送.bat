@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title big 专供推送 GUI
echo ========================================
echo  big 专供推送 GUI
echo  商品搜索推送 + 物流双节奏监控
echo  目录: %CD%
echo ========================================
echo.

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

call "%~dp0..\_find_python.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo 启动 app.py ...
"!PYTHON_EXE!" app.py %*
set "EC=!ERRORLEVEL!"
echo.
if not "!EC!"=="0" (
  echo GUI 异常退出 code=!EC!
) else (
  echo GUI 已关闭。
)
pause
exit /b !EC!
