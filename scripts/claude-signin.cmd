@echo off
rem Sign the WSL Claude CLI in for Roughcut (a year-long token). Double-click me.
rem All the work is in claude-signin.sh, next to this file.
rem "%~dp0." not "%~dp0": a trailing backslash would escape the closing quote.
wsl.exe --cd "%~dp0." -e bash -l claude-signin.sh
echo.
pause
