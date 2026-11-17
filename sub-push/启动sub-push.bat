@echo off
chcp 65001 >nul
cd /d "%~dp0"
title sub-push 上新订阅守护
echo ========================================
echo  启动新版窗口：后端订阅视图 + 守护 + 飞书
echo  请勿再打开旧的 gui.py（那是配置中心）
echo ========================================
python app.py
if errorlevel 1 (
  echo.
  echo 启动失败：请确认本机 python 可用。
  pause
)
