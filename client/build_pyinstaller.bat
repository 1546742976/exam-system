@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 用 PyInstaller 打包（体积小、速度快，但更容易被反解） ===
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

%PY% -m pip install pyinstaller
%PY% -m PyInstaller --noconfirm --clean ^
  --name ckyf_exam ^
  --onedir --windowed ^
  --add-data "static;static" ^
  --add-data "keys;keys" ^
  --add-data "config.json;." ^
  --collect-submodules av ^
  --collect-submodules cv2 ^
  main.py

echo.
echo 产物在 dist\ckyf_exam\ ，把整个目录打包分发。
echo 建议：要防反解就用 build_nuitka.bat，PyInstaller 只适合内部快速验证。
pause
