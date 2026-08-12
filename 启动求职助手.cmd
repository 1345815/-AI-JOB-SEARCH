@echo off
chcp 65001 >nul
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890
set WORKBUDDY_CONFIG_DIR=%USERPROFILE%\.workbuddy
set CODEBUDDY_CONFIG_DIR=%USERPROFILE%\.workbuddy
cd /d "%~dp0"

set "NODE_EXE=%USERPROFILE%\.workbuddy\binaries\node\versions\22.22.2\node.exe"
if not exist "%NODE_EXE%" (
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do set "NODE_EXE=%%D\node.exe"
)

set "CODEBUDDY=%LOCALAPPDATA%\Programs\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy"
if not exist "%CODEBUDDY%" (
  echo WorkBuddy not found. Please install WorkBuddy first.
  pause
  exit /b 1
)

echo Starting AI Job Search on WorkBuddy (CodeBuddy CLI)...
"%NODE_EXE%" "%CODEBUDDY%" %*
pause