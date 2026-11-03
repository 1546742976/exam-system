@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 用 Nuitka 编译为 C 再打包（比 PyInstaller 更难反解） ===
if not exist ".venv\Scripts\python.exe" (
  echo 建议先建虚拟环境： python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
)
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

%PY% -m pip install nuitka ordered-set zstandard
%PY% -m nuitka --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=pywebview ^
  --windows-console-mode=disable ^
  --include-data-dir=static=static ^
  --include-data-dir=keys=keys ^
  --include-data-file=config.json=config.json ^
  --include-package=av --include-package=cv2 --include-package=mss ^
  --nofollow-import-to=tkinter --nofollow-import-to=unittest ^
  --output-dir=dist ^
  --company-name="Exam" --product-name="ckyf_exam" --file-version=3.0.0.0 ^
  main.py

echo.
echo 产物在 dist\main.dist\ ，双击 main.exe 运行。
echo 分发时把 dist\main.dist 整个文件夹打成压缩包发给学生（不要只发 exe）。
pause
