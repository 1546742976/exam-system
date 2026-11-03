"""服务端最小冒烟测试：假 OJ + 网关，跑通登录/门禁/告警/上传/控制台。

用法：python tools/smoke_test.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "client"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse  # noqa: E402

import db  # noqa: E402
import gateway  # noqa: E402

OK, BAD = "[ OK ]", "[FAIL]"
R = []


def check(name, ok, detail=""):
    R.append((name, bool(ok), str(detail)))
    print("  %s %-38s %s" % (OK if ok else BAD, name, detail))


def wait_port(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def serve(app, port, name):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, name=name, daemon=True).start()
    if not wait_port(port):
        raise RuntimeError("%s 未启动" % name)


def fake_oj():
    app = FastAPI()

    @app.post("/login")
    async def login(request: Request):
        body = await request.body()
        from urllib.parse import parse_qs
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        if form.get("password") != "right":
            return HTMLResponse("bad", status_code=200)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("oj_session", "s-" + form.get("uname", ""))
        return resp

    @app.get("/d/system/contest/{tid}")
    def contest(tid: str):
        return HTMLResponse("<h1>CONTEST %s</h1>" % tid)

    @app.get("/d/user")
    def user():
        return HTMLResponse("<h1>USER</h1>")

    @app.get("/hostcheck")
    def hostcheck(request: Request):
        return PlainTextResponse(request.headers.get("host", ""))

    return app


def main() -> int:
    gw, oj = 18901, 18902
    work = BASE / "data" / "smoke"
    work.mkdir(parents=True, exist_ok=True)
    # 冒烟测试必须可重复运行：每次启动前清空测试库，避免上一轮数据影响缺口统计。
    for name in ("smoke.db", "smoke.db-wal", "smoke.db-shm"):
        try:
            (work / name).unlink()
        except FileNotFoundError:
            pass
    cfg = gateway.ServerConfig()
    cfg.host, cfg.port = "127.0.0.1", gw
    cfg.upstream = "http://127.0.0.1:%d" % oj
    cfg.db_path = str(work / "smoke.db")
    cfg.admin_password = "smoke-pass"
    cfg.allowlist_path = str(work / "allowlist.json")
    cfg.login_path = "/login"

    serve(fake_oj(), oj, "oj")
    store = db.Store(cfg.db_path)
    serve(gateway.build_app(cfg, store), gw, "gw")

    print("=" * 62)
    print("服务端冒烟测试  http://127.0.0.1:%d" % gw)
    print("=" * 62)
    c = httpx.Client(base_url="http://127.0.0.1:%d" % gw, timeout=15, follow_redirects=False)

    r = c.get("/exam/admin")
    check("控制台页面可访问", r.status_code == 200 and "监考控制台" in r.text, r.status_code)

    r = c.post("/login", data={"uname": "zhangsan", "password": "right"})
    check("登录成功(302+gw_user)", r.status_code == 302 and "gw_user" in r.cookies, r.status_code)
    key = r.headers.get("X-Exam-Upload-Key", "")
    check("登录响应头下发上传密钥", len(key) > 20, key[:8])

    r = c.get("/d/system/contest/1")
    check("无考试上下文被门禁拦(403)", r.status_code == 403, r.status_code)

    r = c.get("/exam/")
    check("考试入口重定向(302)", r.status_code == 302, r.status_code)
    r = c.get("/d/system/contest/1")
    check("带上下文访问题库(200)", r.status_code == 200 and "CONTEST" in r.text, r.status_code)

    r = c.get("/d/user")
    check("普通路径不受门禁影响(200)", r.status_code == 200, r.status_code)

    r = c.get("/hostcheck", headers={"Host": "10.20.0.105:8888"})
    check("反代保留原始 Host（CSRF）", r.text == "10.20.0.105:8888", r.text)

    body = b"payload" * 100
    sig = hmac.new(key.encode(), digestmod=hashlib.sha256)
    sig.update(b"zhangsan|1|"); sig.update(body)
    r = c.post("/exam/upload?seq=1&kind=ts&name=a.ts", content=body,
               headers={"X-Exam-Sig": sig.hexdigest()})
    check("上传(签名正确 200)", r.status_code == 200, r.status_code)

    r = c.post("/exam/upload?seq=2&kind=ts&name=b.ts", content=b"x",
               headers={"X-Exam-Sig": "0" * 64})
    check("上传(伪造签名 403)", r.status_code == 403, r.status_code)

    for s in (3, 4):
        b2 = ("p%d" % s).encode()
        sg = hmac.new(key.encode(), digestmod=hashlib.sha256)
        sg.update(("zhangsan|%d|" % s).encode()); sg.update(b2)
        c.post("/exam/upload?seq=%d&kind=ts&name=c%d.ts" % (s, s), content=b2,
               headers={"X-Exam-Sig": sg.hexdigest()})

    for hb in (1, 2, 5):
        r = c.post("/exam/alert", json={"alerts": json.dumps(
            [{"id": "ai-port-11434", "text": "本机 11434 有 AI 服务"}]), "hb_seq": hb})
    check("告警上报(双重编码兼容)", r.status_code == 200, r.status_code)

    a = httpx.Client(base_url="http://127.0.0.1:%d" % gw, timeout=15, follow_redirects=False)
    r = a.post("/exam/admin/login", data={"password": "smoke-pass"})
    check("控制台登录", r.status_code == 200, r.status_code)
    r = a.get("/d/system/contest/1")
    check("管理员可绕过学生门禁管理比赛", r.status_code == 200 and "CONTEST" in r.text, r.status_code)
    d = a.get("/exam/admin/api/overview").json()
    st = {s["uname"]: s for s in d["students"]}.get("zhangsan", {})
    check("心跳缺口检出(=2)", st.get("hb_gaps") == 2, st.get("hb_gaps"))
    up = {u["uname"]: u for u in d["uploads"]}.get("zhangsan", {})
    check("上传缺口检出(=1)", up.get("gaps") == 1, up.get("gaps"))
    check("告警已入库", any(x["kind"] == "ai" for x in d["alert_summary"]),
          d["alert_summary"])

    passed = sum(1 for _, ok, _ in R if ok)
    print("=" * 62)
    print("结果：%d/%d 通过" % (passed, len(R)))
    return 0 if passed == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
