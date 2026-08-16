@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="

rem 1. Codex / CodeBuddy 自带 Python
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" (
  for %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
)
if not exist "%PYTHON_EXE%" (
  if exist "%USERPROFILE%\.workbuddy\binaries\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.workbuddy\binaries\python\python.exe"
)

rem 2. 系统 Python
if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul && set "PYTHON_EXE=python"
)
if not exist "%PYTHON_EXE%" (
  where py >nul 2>nul && for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)"') do set "PYTHON_EXE=%%P"
)

if not exist "%PYTHON_EXE%" (
  echo [错误] 未找到 Python。请安装 Python 3.10+ 并勾选 "Add to PATH"，或安装 WorkBuddy/Codex。
  pause
  exit /b 1
)

echo 使用 Python: %PYTHON_EXE%
echo 正在启动 CareerPilot Web（按 Ctrl+C 停止）...
start "" http://127.0.0.1:8000
"%PYTHON_EXE%" "web\server.py" --port 8000
endlocal
