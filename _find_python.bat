@echo off
REM 供其它 bat call：选出可用的 Python，结果写入 PYTHON_EXE
REM 优先级：当前目录 .venv → py -3 → 非 WindowsApps 的 python.exe
set "PYTHON_EXE="

if exist "%CD%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
  goto :_verify
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    set "PYTHON_EXE=%%I"
  )
  if defined PYTHON_EXE goto :_verify
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  for /f "delims=" %%I in ('where python') do (
    echo %%I | find /i "\WindowsApps\" >nul
    if errorlevel 1 (
      set "PYTHON_EXE=%%I"
      goto :_verify
    )
  )
)

echo [错误] 未找到可用 Python。请安装 Python3 并加入 PATH，或在项目下创建 .venv
exit /b 1

:_verify
echo 使用 Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import sys; print('version=', sys.version.split()[0])"
if errorlevel 1 (
  echo [错误] Python 无法运行: %PYTHON_EXE%
  set "PYTHON_EXE="
  exit /b 1
)
exit /b 0
