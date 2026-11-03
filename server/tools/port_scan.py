"""局域网端口基线巡检（考中）。正常只应看到 34519。

用法：python port_scan.py 10.20.0.0/24
"""
import concurrent.futures as cf
import ipaddress
import socket
import sys

PORTS = {34519: "ckyf-client(正常)", 5900: "VNC", 3389: "RDP", 5938: "TeamViewer",
         8000: "向日葵", 8080: "代理/共享", 445: "SMB", 139: "NetBIOS"}


def probe(host, port, timeout=0.3):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def main(net: str) -> None:
    hosts = [str(h) for h in ipaddress.ip_network(net, strict=False).hosts()]
    print("扫描 %s，共 %d 台" % (net, len(hosts)))
    bad = 0
    with cf.ThreadPoolExecutor(max_workers=128) as pool:
        jobs = {(h, p): pool.submit(probe, h, p) for h in hosts for p in PORTS}
        for (h, p), fut in jobs.items():
            try:
                if fut.result():
                    if p != 34519:
                        bad += 1
                    print("  %-15s %5d  %s" % (h, p, PORTS[p]))
            except Exception:
                pass
    print("完成，异常端口命中 %d 个" % bad)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "10.20.0.0/24")
