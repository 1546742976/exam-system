"""生成/查看客户端指纹白名单。改过 client/*.py 后必须重跑。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ALLOWLIST = BASE / "data" / "allowlist.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("client_dir", nargs="?", default=str(BASE.parent / "client"))
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    current = []
    if ALLOWLIST.exists():
        try:
            data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
            current = data if isinstance(data, list) else data.get("fingerprints", [])
        except Exception:
            current = []
    if args.show:
        print("当前白名单:", current or "（空，表示只记录不拦截）")
        return

    client_dir = Path(args.client_dir).resolve()
    sys.path.insert(0, str(client_dir))
    import fingerprint
    value = fingerprint.compute(client_dir)
    print("客户端目录:", client_dir)
    print("指纹:", value)
    items = list(current) if args.append else []
    if value not in items:
        items.append(value)
    ALLOWLIST.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入", ALLOWLIST)
    print("白名单:", items)


if __name__ == "__main__":
    main()
