"""客户端自身指纹：把本目录下的 .py 与 keys/*.pem 内容哈希成一个短串。

用途：登录时随 /exam/session 一起上报，服务端比对白名单。
学生只要改动任何一行客户端代码，指纹就会变 → 控制台标红（可举证）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def compute(root: Path = None) -> str:
    base = Path(root) if root else ROOT
    files = sorted(base.glob("*.py"))
    keys = base / "keys"
    if keys.is_dir():
        files += sorted(keys.glob("*.pem"))
    digest = hashlib.sha256()
    for path in files:
        try:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
        except Exception:
            continue
    return digest.hexdigest()[:32]


def details(root: Path = None) -> dict:
    """逐文件哈希，便于排查到底哪个文件被改。"""
    base = Path(root) if root else ROOT
    out = {}
    for path in sorted(base.glob("*.py")) + sorted((base / "keys").glob("*.pem")):
        try:
            out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except Exception:
            continue
    return out


if __name__ == "__main__":
    print("fingerprint:", compute())
    for name, value in details().items():
        print("  %-20s %s" % (name, value))
