@echo off
setlocal

REM 需要先在当前 venv 安装 pyinstaller:
REM   pip install pyinstaller

cd /d %~dp0

pyinstaller --noconfirm --onefile --windowed ^
  --name sub-push ^
  app.py

echo.
echo Build done. Output: dist\sub-push.exe
pause

