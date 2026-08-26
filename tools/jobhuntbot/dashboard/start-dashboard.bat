@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "http://localhost:8420/dashboard.html"
node server.js
pause
