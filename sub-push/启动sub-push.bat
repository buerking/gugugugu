@echo off
chcp 65001 >nul
REM 兼容：转到仓库根目录「启动全部.bat」
cd /d "%~dp0.."
call "%~dp0..\启动全部.bat" %*
