"""Reproducible Windows build; use .venv-build, then .venv, then launcher Python."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    os.chdir(ROOT)
    os.environ['PYTHONUTF8'] = '1'
    os.environ['NUITKA_CACHE_DIR'] = str(ROOT / '.build-cache' / 'nuitka')
    mode = sys.argv[1] if len(sys.argv) > 1 else 'nuitka'
    for candidate in (ROOT / '.venv-build/Scripts/python.exe', ROOT / '.venv/Scripts/python.exe', Path(sys.executable)):
        if candidate.exists() and subprocess.run([str(candidate), '-c', 'import sys'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            python = str(candidate)
            break
    else:
        raise SystemExit('No usable Python; create .venv-build using Python 3.11 x64')
    if mode == 'nuitka':
        args = ['-m', 'nuitka', '--standalone', '--assume-yes-for-downloads', '--windows-console-mode=disable', '--include-data-dir=static=static', '--include-data-dir=keys=keys', '--include-data-file=config.json=config.json', '--include-package=av', '--include-package=cv2', '--include-package=mss', '--nofollow-import-to=tkinter', '--nofollow-import-to=unittest', '--output-dir=dist', '--company-name=Exam', '--product-name=ckyf_exam', '--file-version=3.0.1.0', 'main.py']
    elif mode == 'pyinstaller':
        args = ['-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'ckyf_exam', '--onedir', '--windowed', '--add-data', 'static;static', '--add-data', 'keys;keys', '--add-data', 'config.json;.', '--collect-submodules', 'av', '--collect-submodules', 'cv2', 'main.py']
    else:
        raise SystemExit('Expected nuitka or pyinstaller')
    return subprocess.call([python, '-X', 'utf8', *args])

if __name__ == '__main__':
    raise SystemExit(main())
