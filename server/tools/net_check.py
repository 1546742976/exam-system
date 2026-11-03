"""考场局域网体检（NUC 上跑）。

用法：python net_check.py --expect 10.20.0.0/24 --gw 10.20.0.1 --peers 10.20.0.101
"""
import argparse
import ipaddress
import socket
import sys
import urllib.error
import urllib.request

R = []


def check(name, ok, detail=""):
    R.append((name, bool(ok), str(detail)))
    print("  [%s] %-40s %s" % ("PASS" if ok else "FAIL", name, detail))


def tcp(host, port, t=0.5):
    s = socket.socket()
    s.settimeout(t)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def my_ip(net=None):
    """优先返回落在预期考场网段内的本机 IPv4。"""
    candidates = []
    try:
        import psutil
        for _nic, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    candidates.append((a.address, a.netmask or ""))
    except Exception:
        pass
    if net is not None:
        for ip, mask in candidates:
            try:
                if ipaddress.ip_address(ip) in net:
                    return ip, mask
            except Exception:
                pass
    if candidates:
        return candidates[0]
    return "", ""


def http_get(url, timeout=1.5):
    req = urllib.request.Request(url, headers={"User-Agent": "ckyf-net-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", default="10.20.0.0/24")
    ap.add_argument("--gw", default="10.20.0.1")
    ap.add_argument("--peers", default="")
    ap.add_argument("--gateway-port", type=int, default=8888)
    args = ap.parse_args()

    print("=" * 68)
    print("考场局域网体检")
    print("=" * 68)
    net = ipaddress.ip_network(args.expect, strict=False)
    ip, mask = my_ip(net)
    print("\n[1] 本机：%s / %s" % (ip or "-", mask or "-"))
    check("在预期网段 %s" % args.expect, bool(ip) and ipaddress.ip_address(ip) in net, ip)
    check("默认网关 %s 可达" % args.gw, tcp(args.gw, 80, 0.6) or tcp(args.gw, 443, 0.6))

    print("\n[2] 考试网关服务")
    status, body = http_get("http://127.0.0.1:%d/exam/admin" % args.gateway_port)
    check("网关控制台可访问（不是普通 OJ 端口）",
          status == 200 and "监考控制台" in body.decode("utf-8", "ignore"),
          "HTTP %s" % status)
    print("     学生填的地址：http://%s:%d" % (ip, args.gateway_port))

    print("\n[3] 外网（断网考场应不通）")
    if tcp("223.5.5.5", 443, 1.2):
        print("  [WARN] NUC 本机能上外网（教师管理网可保留）")
        print("         学生机是否断网必须在学生机上调 net_check / curl 实测。")
    else:
        print("  [ OK ] NUC 本机外网不通")

    print("\n[4] 学生机 34519（教师抓图依赖）")
    peers = [p.strip() for p in args.peers.split(",") if p.strip()]
    if not peers:
        print("     （用 --peers 10.20.0.101,10.20.0.102 指定）")
    for p in peers:
        check("可访问 %s:34519" % p, tcp(p, 34519, 0.8))

    passed = sum(1 for _, ok, _ in R if ok)
    print("\n结果：%d/%d 通过" % (passed, len(R)))
    for n, ok, d in R:
        if not ok:
            print("  需处理：%s | %s" % (n, d))
    return 0 if passed == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
