@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
python build.py nuitka
exit /b %errorlevel%
