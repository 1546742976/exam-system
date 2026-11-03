"""生成客户端/服务端共享密钥。优先 cryptography，其次系统 openssl。

用法： python gen_keys.py [--bits 2048]
写入： ../client/keys/public.pem   ./keys/private.pem  ./keys/public.pem
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
CLIENT_KEYS = BASE.parent / "client" / "keys"
SERVER_KEYS = BASE / "keys"


def gen_cryptography(bits: int):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def gen_openssl(bits: int):
    if not shutil.which("openssl"):
        raise RuntimeError("既没有 cryptography 也没有 openssl，请 pip install cryptography")
    SERVER_KEYS.mkdir(parents=True, exist_ok=True)
    priv_path, pub_path = SERVER_KEYS / "private.pem", SERVER_KEYS / "public.pem"
    subprocess.check_call(["openssl", "genpkey", "-algorithm", "RSA",
                           "-pkeyopt", "rsa_keygen_bits:%d" % bits, "-out", str(priv_path)])
    subprocess.check_call(["openssl", "pkey", "-in", str(priv_path), "-pubout",
                           "-out", str(pub_path)])
    return priv_path.read_text(encoding="utf-8"), pub_path.read_text(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bits", type=int, default=2048)
    args = ap.parse_args()
    CLIENT_KEYS.mkdir(parents=True, exist_ok=True)
    SERVER_KEYS.mkdir(parents=True, exist_ok=True)
    try:
        priv, pub = gen_cryptography(args.bits)
    except ImportError:
        priv, pub = gen_openssl(args.bits)
    (SERVER_KEYS / "private.pem").write_text(priv, encoding="utf-8")
    (SERVER_KEYS / "public.pem").write_text(pub, encoding="utf-8")
    (CLIENT_KEYS / "public.pem").write_text(pub, encoding="utf-8")
    print("已生成 RSA %d 位密钥对" % args.bits)
    print("  server/keys/private.pem  （只留服务器）")
    print("  server/keys/public.pem + client/keys/public.pem")
    print()
    print("若改用 HydroOJ 插件（addon/key.ts）自带的密钥，请改用其公钥覆盖")
    print("client/keys/public.pem，并把 client/config.json 的 jwt_audience 置空。")


if __name__ == "__main__":
    main()
