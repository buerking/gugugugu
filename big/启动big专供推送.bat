@echo off
chcp 65001 >nul
cd /d "%~dp0"

title big 专供推送 GUI
echo ========================================
echo  big 专供推送 GUI
echo  商品搜索推送 + 物流双节奏监控
echo  目录: %CD%
echo ========================================
echo.

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python app.py
) else (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 app.py
  ) else (
    echo [错误] 未找到 python，请先安装或加入 PATH
    pause
    exit /b 1
  )
)

echo.
echo GUI 已关闭。
pause
