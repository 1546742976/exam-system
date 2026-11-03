"""校验录像切片签名。用法：python verify_sig.py <录像目录> <用户名> <sign_key>"""
import hashlib
import hmac
import sys
from pathlib import Path


def main(folder: str, sid: str, key: str) -> None:
    files = sorted(Path(folder).glob("video_*.ts"))
    if not files:
        print("没找到切片:", folder)
        return
    bad = 0
    for ts in files:
        h = hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256)
        h.update(sid.encode("utf-8"))
        h.update(ts.read_bytes())
        sig = ts.with_suffix(ts.suffix + ".sig")
        actual = sig.read_text(encoding="utf-8").strip() if sig.exists() else ""
        ok = hmac.compare_digest(h.hexdigest(), actual)
        bad += 0 if ok else 1
        print("%-22s %s" % (ts.name, "OK" if ok else "MISMATCH"))
    print("共 %d 片，异常 %d 片" % (len(files), bad))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
