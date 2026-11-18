@echo off
chcp 65001 >nul
REM 兼容旧入口：转调「启动全部.bat」（公共 + VIP + 物流）
cd /d "%~dp0"
call "%~dp0启动全部.bat" %*
