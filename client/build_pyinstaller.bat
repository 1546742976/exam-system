@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python build.py pyinstaller
exit /b %errorlevel%
