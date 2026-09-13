@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py start_all.py
) else (
  python start_all.py
)
endlocal
