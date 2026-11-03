"""考试网关：反代 HydroOJ + 考试入口 + 告警/上传收集 + 教师控制台。

拓扑：
    学生机(客户端) ──> 本服务(:8888) ──> HydroOJ(:8889)
    教师浏览器   ──> 本服务 /exam/admin ──> 学生机 :34519（用 teacher_jwt）
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)

from db import Store

BASE_DIR = Path(__file__).resolve().parent
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "content-length",
               "content-encoding"}

# 登录时下发、客户端用它给上传签名；只存内存，网关重启需重新登录
SESSIONS = {}
UPLOAD_DIR = BASE_DIR / "data" / "uploads"


def _load_env_file(path: Path) -> None:
    """加载极简 KEY=VALUE 环境文件；不覆盖已有环境变量。"""
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        print("读取 .env 失败:", exc)


_load_env_file(BASE_DIR / ".env")


class ServerConfig:
    def __init__(self, path: Path = None):
        self.host = "0.0.0.0"
        self.port = 8888
        self.upstream = "http://127.0.0.1:8889"
        self.db_path = "data/exam.db"
        self.contest_url = "/d/system/contest/"
        self.gate_enabled = True
        self.gate_path_keywords = ["/contest", "/p/", "/submit"]
        self.login_path = "/login"
        self.jwt_alg = "RS256"
        self.jwt_private_key_file = "keys/private.pem"
        self.jwt_public_key_file = "keys/public.pem"
        self.jwt_secret = ""
        self.jwt_audience = "ckyf-exam"
        self.teacher_token_ttl = 3600
        self.sign_key = "CHANGE_ME_sign_key"
        self.admin_password = "admin-CHANGE-ME"
        self.session_secret = "CHANGE_ME_session_secret"
        self.session_alg = "HS256"
        self.cookie_name = "gw_user"
        self.exam_cookie = "exam_sess"
        self.student_port = 34519
        self.login_concurrency = 16
        self.async_db = True
        self.silence_seconds = 40
        self.fingerprint_enforce = False
        self.allowlist_path = "data/allowlist.json"
        self.upload_required = True
        self.upload_max_mb = 8
        # 强制把转发给 OJ 的 UA 标成考试客户端（addon 靠 UA 放行）
        self.force_client_ua = "exam-client/2.0"
        # 默认不信任客户端自带的 X-Forwarded-For。
        # 只有网关确实部署在反向代理后面时才开启，并把代理地址加入 trusted_proxies。
        self.trust_proxy_headers = False
        self.trusted_proxies = ["127.0.0.1", "::1"]
        p = path or (BASE_DIR / "config.json")
        if p.exists():
            try:
                for k, v in json.loads(p.read_text(encoding="utf-8")).items():
                    if hasattr(self, k):
                        setattr(self, k, v)
            except Exception as exc:
                print("config.json 解析失败，用默认值:", exc)

        # 生产密钥优先从环境变量/.env 读取，避免写进可分发源码。
        for env_key, attr in (
            ("CKYF_HOST", "host"),
            ("CKYF_PORT", "port"),
            ("CKYF_UPSTREAM", "upstream"),
            ("CKYF_CONTEST_URL", "contest_url"),
            ("CKYF_ADMIN_PASSWORD", "admin_password"),
            ("CKYF_SESSION_SECRET", "session_secret"),
            ("CKYF_SIGN_KEY", "sign_key"),
            ("CKYF_JWT_PRIVATE_KEY_FILE", "jwt_private_key_file"),
            ("CKYF_JWT_PUBLIC_KEY_FILE", "jwt_public_key_file"),
        ):
            if os.environ.get(env_key):
                value = os.environ[env_key]
                setattr(self, attr, int(value) if attr == "port" else value)
        if os.environ.get("CKYF_TRUST_PROXY_HEADERS"):
            self.trust_proxy_headers = os.environ["CKYF_TRUST_PROXY_HEADERS"].lower() in (
                "1", "true", "yes", "on")
        if os.environ.get("CKYF_TRUSTED_PROXIES"):
            self.trusted_proxies = [x.strip() for x in os.environ["CKYF_TRUSTED_PROXIES"].split(",")
                                    if x.strip()]
        self.upstream = self.upstream.rstrip("/")


# ---------------------------------------------------------------- 令牌
_KEY_CACHE = {}


def _load_private_key(cfg: ServerConfig):
    path = str(BASE_DIR / cfg.jwt_private_key_file)
    key = _KEY_CACHE.get(path)
    if key is None:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        _KEY_CACHE[path] = key
    return key


def _load_public_key(cfg: ServerConfig):
    path = str(BASE_DIR / cfg.jwt_public_key_file)
    key = _KEY_CACHE.get(path)
    if key is None:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_public_key(Path(path).read_bytes())
        _KEY_CACHE[path] = key
    return key


def sign_token(cfg: ServerConfig, payload: dict) -> str:
    """教师令牌用 RS256（客户端要用公钥验签）。"""
    import jwt as pyjwt
    if cfg.jwt_alg.upper() == "HS256":
        return pyjwt.encode(payload, cfg.jwt_secret, algorithm="HS256")
    return pyjwt.encode(payload, _load_private_key(cfg), algorithm="RS256")


def make_session(cfg: ServerConfig, data: dict, ttl: int = 43200) -> str:
    """会话 Cookie 只在本网关内部使用，用 HMAC-SHA256 就够（快 ~30 倍）。"""
    import jwt as pyjwt
    payload = dict(data)
    payload["exp"] = int(time.time()) + ttl
    secret = cfg.session_secret or cfg.jwt_secret or "ckyf-session-fallback"
    return pyjwt.encode(payload, secret, algorithm="HS256")


def read_session(cfg: ServerConfig, token: str) -> dict:
    import jwt as pyjwt
    secret = cfg.session_secret or cfg.jwt_secret or "ckyf-session-fallback"
    try:
        return pyjwt.decode(token, secret, algorithms=["HS256"],
                            options={"verify_aud": False, "require": ["exp", "sub", "role"]})
    except Exception:
        pass
    return {}


def _normalize_ip(value: str) -> str:
    """返回规范化 IP；非法值返回空字符串。"""
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except Exception:
        return ""


def client_ip(request: Request, cfg: ServerConfig = None) -> str:
    """获取可信客户端 IP。

    默认只使用 TCP 对端地址。只有显式开启 trust_proxy_headers，且 TCP 对端在
    trusted_proxies 中时，才读取 X-Forwarded-For，防止学生伪造来源 IP。
    """
    peer = _normalize_ip(request.client.host if request.client else "")
    if cfg and cfg.trust_proxy_headers and peer:
        trusted = {_normalize_ip(x) for x in cfg.trusted_proxies}
        if peer in trusted:
            for candidate in request.headers.get("x-forwarded-for", "").split(","):
                ip = _normalize_ip(candidate)
                if ip:
                    return ip
    return peer


def build_app(cfg: ServerConfig, store: Store) -> FastAPI:
    app = FastAPI(title="ckyf exam gateway", docs_url=None, redoc_url=None)
    upstream_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_connections=300, max_keepalive_connections=150),
        follow_redirects=False, trust_env=False)
    login_sem = asyncio.Semaphore(max(1, int(cfg.login_concurrency)))
    @app.on_event("startup")
    async def _warmup():
        try:
            await upstream_client.get(cfg.upstream + "/", timeout=5)
        except Exception:
            pass

    @app.on_event("shutdown")
    async def _close_client():
        task = getattr(app.state, "watchdog", None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await upstream_client.aclose()

    # -------------------------------------------------- 登录（转发给 OJ）
    @app.post("/login")
    async def login(request: Request):
        ip = client_ip(request, cfg)
        body = await request.body()
        ua = request.headers.get("user-agent", cfg.force_client_ua)
        headers = {"Content-Type": request.headers.get(
                       "content-type", "application/x-www-form-urlencoded"),
                   "User-Agent": ua}
        original_host = request.headers.get("host", "")
        if original_host:
            headers["Host"] = original_host
            headers["X-Forwarded-Host"] = original_host
        headers["X-Forwarded-Proto"] = request.url.scheme
        async with login_sem:
            resp = await upstream_client.post(cfg.upstream + cfg.login_path,
                                              content=body, headers=headers)
        form = _parse_form(body)
        uname = form.get("uname", "")
        out = Response(content=resp.content, status_code=resp.status_code)
        _copy_headers(resp, out)
        if resp.status_code in (301, 302, 303):
            token = make_session(cfg, {"sub": uname, "ip": ip, "role": "student"})
            out.set_cookie(cfg.cookie_name, token, httponly=False, samesite="lax",
                           max_age=43200)
            key = secrets.token_urlsafe(32)
            SESSIONS[uname] = {"key": key, "issued": int(time.time()), "ip": ip}
            out.headers["X-Exam-Upload-Key"] = key
            out.headers["X-Exam-Upload-Url"] = "/exam/upload"
            store.touch_student(uname, ip, "")
            store.log_event("login_ok", uname, ip, "UA=" + ua)
        else:
            store.log_event("login_fail", uname, ip, "status=%d" % resp.status_code)
        return out

    # -------------------------------------------------- 考试入口
    @app.get("/exam/")
    @app.get("/exam")
    async def exam_entry(request: Request):
        sess = read_session(cfg, request.cookies.get(cfg.cookie_name, ""))
        if not sess.get("sub") or sess.get("role") != "student":
            return HTMLResponse(_page(
                "请通过监考客户端进入",
                "未检测到有效登录状态。请启动监考客户端，在客户端里登录后会自动跳转到这里。"),
                status_code=401)
        uname = sess["sub"]
        if cfg.fingerprint_enforce and not SESSIONS.get(uname, {}).get("fp_ok"):
            return JSONResponse({"detail": "fingerprint required"}, status_code=403)
        contest = _contest_id(request)
        token = make_session(cfg, {"sub": uname, "role": "exam", "contest": contest})
        target = cfg.contest_url.rstrip("/") + "/" + (contest or "")
        store.log_event("exam_enter", uname, client_ip(request, cfg), target)
        resp = RedirectResponse(target, status_code=302)
        resp.set_cookie(cfg.exam_cookie, token, httponly=True, samesite="lax",
                        max_age=6 * 3600)
        return resp

    # -------------------------------------------------- 告警
    @app.post("/exam/alert")
    async def exam_alert(request: Request):
        sess = read_session(cfg, request.cookies.get(cfg.cookie_name, ""))
        ip = client_ip(request, cfg)
        if not sess.get("sub") or sess.get("role") != "student":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return JSONResponse({"ok": False, "detail": "invalid json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "object required"}, status_code=400)
        try:
            int(payload.get("hb_seq") or 0)
            int(payload.get("client_time") or 0)
        except (ValueError, TypeError):
            return JSONResponse({"detail": "invalid sequence/time"}, status_code=400)
        alerts = payload.get("alerts")
        if isinstance(alerts, str):
            try:
                alerts = json.loads(alerts)
            except Exception:
                alerts = []
        if not isinstance(alerts, list):
            alerts = payload.get("alerts_list") or []
        if not isinstance(alerts, list):
            alerts = []
        if len(alerts) > 100 or any(not isinstance(x, dict) for x in alerts):
            return JSONResponse({"detail": "invalid alerts"}, status_code=400)
        uname = sess["sub"]
        if not sess.get("sub") or sess.get("role") != "student":
            store.log_event("alert_no_session", uname, ip,
                            raw[:200].decode("utf-8", "ignore"))
        store.touch_student(uname, ip, payload.get("endpoint", ""))
        hb_seq = int(payload.get("hb_seq") or 0)
        if hb_seq:
            gap = store.record_heartbeat(uname, hb_seq, ip)
            if gap > 0:
                store.log_event("hb_gap", uname, ip,
                                "seq=%d 缺 %d 次心跳" % (hb_seq, gap))
        n = store.save_alerts(uname, ip, alerts, payload.get("health") or {},
                              int(payload.get("client_time") or 0))
        return JSONResponse({"ok": True, "saved": n})

    # -------------------------------------------------- 会话密钥
    @app.get("/exam/session")
    async def exam_session(request: Request, fp: str = ""):
        sess = read_session(cfg, request.cookies.get(cfg.cookie_name, ""))
        if not sess.get("sub") or sess.get("role") != "student":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        uname = sess["sub"]
        key = secrets.token_urlsafe(32)
        SESSIONS[uname] = {"key": key, "issued": int(time.time()),
                           "ip": client_ip(request, cfg)}
        fp_ok = _check_fingerprint(cfg, store, uname, fp, client_ip(request, cfg))
        if cfg.fingerprint_enforce and not fp_ok:
            SESSIONS.pop(uname, None)
            return JSONResponse({"detail": "fingerprint rejected"}, status_code=403)
        SESSIONS[uname]["fp_ok"] = fp_ok
        store.log_event("session_issued", uname, client_ip(request, cfg),
                        "fp=%s ok=%s" % (fp[:12], fp_ok))
        return JSONResponse({"uname": uname, "upload_key": key,
                             "upload_url": "/exam/upload",
                             "segment_seconds": 3,
                             "required": cfg.upload_required,
                             "fingerprint_ok": fp_ok})

    # -------------------------------------------------- 上传（边录边传）
    @app.post("/exam/upload")
    async def exam_upload(request: Request, seq: int = 0, kind: str = "ts",
                          name: str = ""):
        sess = read_session(cfg, request.cookies.get(cfg.cookie_name, ""))
        uname = sess.get("sub", "")
        ip = client_ip(request, cfg)
        if not uname or sess.get("role") != "student":
            store.log_event("upload_no_session", "", ip, name)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        info = SESSIONS.get(uname)
        signature = request.headers.get("x-exam-sig", "")
        if not info:
            return JSONResponse({"detail": "session expired; login again"}, status_code=401)
        if cfg.fingerprint_enforce and not info.get("fp_ok"):
            return JSONResponse({"detail": "fingerprint required"}, status_code=403)
        if seq < 1 or kind not in ("ts", "frame"):
            return JSONResponse({"detail": "invalid upload metadata"}, status_code=400)
        key = info["key"]
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > cfg.upload_max_mb * 1024 * 1024:
                return JSONResponse({"detail": "payload too large"}, status_code=413)
        expect = hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256)
        expect.update(("%s|%d|" % (uname, seq)).encode("utf-8"))
        expect.update(body)
        if not hmac.compare_digest(expect.hexdigest(), signature):
            store.log_event("upload_bad_sig", uname, ip, "seq=%d" % seq)
            return JSONResponse({"detail": "bad signature"}, status_code=403)
        digest = hashlib.sha256(body).hexdigest()
        safe_name = "".join(c for c in (name or "seg") if c.isalnum() or c in "-_.")[:80]
        day = time.strftime("%Y%m%d")
        folder = UPLOAD_DIR / day / hashlib.sha256(uname.encode("utf-8")).hexdigest()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("%06d_%s_%s" % (seq, digest[:16], safe_name))
        await asyncio.to_thread(target.write_bytes, body)
        store.record_upload(uname, seq, kind, safe_name, len(body), digest, str(target))
        return JSONResponse({"ok": True, "seq": seq, "sha256": digest})

    # -------------------------------------------------- 教师控制台
    @app.post("/exam/admin/login")
    async def admin_login(request: Request):
        form = _parse_form(await request.body())
        if str(form.get("password", "")) != cfg.admin_password:
            store.log_event("admin_login_fail", "", client_ip(request, cfg), "")
            return JSONResponse({"ok": False, "detail": "口令错误"}, status_code=401)
        token = make_session(cfg, {"sub": "admin", "role": "admin"})
        resp = JSONResponse({"ok": True})
        resp.set_cookie("gw_admin", token, httponly=True, samesite="lax", max_age=8 * 3600)
        store.log_event("admin_login_ok", "admin", client_ip(request, cfg), "")
        return resp

    def _require_admin(request: Request) -> bool:
        return read_session(cfg, request.cookies.get("gw_admin", "")).get("role") == "admin"

    @app.get("/exam/admin/api/overview")
    async def admin_overview(request: Request):
        if not _require_admin(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        students = store.list_students()
        return JSONResponse({
            "students": students,
            "online": sum(1 for s in students if s["online"]),
            "total": len(students),
            "alert_summary": store.alert_summary(),
            "same_ip": store.same_ip_students(),
            "uploads": store.upload_stats(),
            "silent_students": store.stale_students(cfg.silence_seconds),
            "fp_stats": {
                "ok": sum(1 for s in students if s.get("fp_ok") == 1),
                "bad": sum(1 for s in students if s.get("fp_ok") == 0),
                "unknown": sum(1 for s in students if s.get("fp_ok") in (-1, None)),
            },
            "alerts": store.recent_alerts(100),
            "events": store.recent_events(50),
        })

    @app.get("/exam/admin/api/token")
    async def admin_token(request: Request, ip: str = "", ttl: int = 0):
        if not _require_admin(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        ttl = max(1, min(ttl or cfg.teacher_token_ttl, cfg.teacher_token_ttl))
        target_ip = _normalize_ip(ip)
        if ip and not target_ip:
            return JSONResponse({"detail": "非法 IP"}, status_code=400)
        token = sign_token(cfg, {"sub": "teacher", "aud": cfg.jwt_audience, "ip": target_ip,
                                 "exp": int(time.time()) + ttl})
        store.log_event("teacher_token", "teacher", client_ip(request, cfg), "target=" + target_ip)
        return JSONResponse({"token": token, "expires_in": ttl})

    @app.get("/exam/admin/api/shot")
    async def admin_shot(request: Request, ip: str = ""):
        if not _require_admin(request):
            return Response(status_code=401)
        target_ip = _normalize_ip(ip)
        if not target_ip:
            return JSONResponse({"detail": "缺少或非法 IP"}, status_code=400)
        token = sign_token(cfg, {"sub": "teacher", "aud": cfg.jwt_audience,
                                 "exp": int(time.time()) + 120})
        try:
            resp = await upstream_client.get(
                "http://%s:%d/screenshot/latest" % (target_ip, cfg.student_port),
                params={"token": token}, timeout=8)
            if resp.status_code == 200:
                return Response(content=resp.content, media_type="image/png",
                                headers={"Cache-Control": "no-store"})
            return Response(status_code=204)
        except Exception as exc:
            store.log_event("shot_fail", "", ip, str(exc)[:200])
            return JSONResponse({"detail": "抓图失败: %s" % exc}, status_code=502)

    @app.get("/exam/admin/api/recorders")
    async def admin_recorders(request: Request, ip: str = ""):
        if not _require_admin(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        target_ip = _normalize_ip(ip)
        if not target_ip:
            return JSONResponse({"detail": "缺少或非法 IP"}, status_code=400)
        token = sign_token(cfg, {"sub": "teacher", "aud": cfg.jwt_audience,
                                 "exp": int(time.time()) + 300})
        try:
            resp = await upstream_client.get(
                "http://%s:%d/recorder/list" % (target_ip, cfg.student_port),
                params={"token": token}, timeout=8)
            return JSONResponse(resp.json())
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/exam/admin/api/challenge")
    async def admin_challenge(request: Request, ip: str = ""):
        if not _require_admin(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        target_ip = _normalize_ip(ip)
        if not target_ip:
            return JSONResponse({"detail": "缺少或非法 IP"}, status_code=400)
        nonce = secrets.token_hex(4).upper()
        token = sign_token(cfg, {"sub": "teacher", "aud": cfg.jwt_audience,
                                 "exp": int(time.time()) + 120})
        try:
            resp = await upstream_client.get(
                "http://%s:%d/probe/frame" % (target_ip, cfg.student_port),
                params={"nonce": nonce, "token": token}, timeout=10)
            if resp.status_code == 200:
                store.log_event("challenge_ok", "", ip, "nonce=" + nonce)
                return Response(content=resp.content, media_type="image/jpeg",
                                headers={"X-Challenge-Nonce": nonce,
                                         "Cache-Control": "no-store"})
            store.log_event("challenge_fail", "", ip, "HTTP %d" % resp.status_code)
            return JSONResponse({"detail": "客户端未响应抽查", "nonce": nonce},
                                status_code=502)
        except Exception as exc:
            store.log_event("challenge_fail", "", ip, str(exc)[:200])
            return JSONResponse({"detail": "抽查失败: %s" % exc}, status_code=502)

    @app.get("/exam/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        page = BASE_DIR / "admin.html"
        if not page.exists():
            return HTMLResponse(_page("缺少 admin.html", str(page)))
        return HTMLResponse(page.read_text(encoding="utf-8"))

    # -------------------------------------------------- 门禁 + 反代
    @app.api_route("/{path:path}",
                   methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    async def proxy(path: str, request: Request):
        if cfg.gate_enabled and _needs_gate(cfg, "/" + path):
            # 监考管理员先用 /exam/admin 的密码登录，拿到 gw_admin Cookie 后
            # 可以在同一浏览器中管理 HydroOJ 比赛；学生没有这个 Cookie。
            admin_sess = read_session(cfg, request.cookies.get("gw_admin", ""))
            is_gateway_admin = admin_sess.get("role") == "admin"
            if not is_gateway_admin:
                sess = read_session(cfg, request.cookies.get(cfg.exam_cookie, ""))
                if sess.get("role") != "exam":
                    store.log_event("gate_block", sess.get("sub", ""), client_ip(request, cfg), path)
                    return HTMLResponse(_page(
                        "请通过监考客户端进入考试",
                        "检测到你在没有考试上下文的情况下直接访问考试页面。<br>"
                        "请关闭浏览器，改用监考客户端登录后点击「下一步」进入。"),
                        status_code=403)
        return await _forward(cfg, request, path, upstream_client)

    _alerted = {}

    @app.on_event("startup")
    async def _start_watchdog():
        app.state.watchdog = asyncio.create_task(_silence_watchdog())

    async def _silence_watchdog():
        while True:
            await asyncio.sleep(10)
            try:
                now = int(time.time())
                for row in store.stale_students(cfg.silence_seconds):
                    uname = row["uname"]
                    if now - _alerted.get(uname, 0) < 300:
                        continue
                    _alerted[uname] = now
                    store.log_event("hb_lost", uname, row.get("ip", ""),
                                    "已静默 %d 秒" % (now - int(row["last_seen"] or now)))
            except Exception:
                pass

    return app


# ---------------------------------------------------------------- 辅助函数

def _parse_form(raw: bytes) -> dict:
    """解析 urlencoded（不依赖 python-multipart）。"""
    from urllib.parse import parse_qs
    try:
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
    except Exception:
        return {}


def _check_fingerprint(cfg: ServerConfig, store: Store, uname: str, fp: str, ip: str) -> bool:
    path = BASE_DIR / cfg.allowlist_path
    allow = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            allow = data if isinstance(data, list) else data.get("fingerprints", [])
        except Exception:
            allow = []
    if not allow:
        store.set_fingerprint(uname, fp, True)
        return not cfg.fingerprint_enforce
    ok = fp in allow
    store.set_fingerprint(uname, fp, ok)
    if not ok:
        store.log_event("fingerprint_mismatch", uname, ip, fp[:16])
    return ok


def _needs_gate(cfg: ServerConfig, path: str) -> bool:
    if path.startswith("/exam") or path.startswith("/static") or path.startswith("/login"):
        return False
    return any(k in path for k in cfg.gate_path_keywords)


def _contest_id(request: Request) -> str:
    for key in ("contest", "tid", "id"):
        if request.query_params.get(key):
            return request.query_params[key]
    return ""


def _copy_headers(resp: httpx.Response, out: Response) -> None:
    raw = []
    for key, value in resp.headers.items():
        low = key.lower()
        if low in HOP_HEADERS or low == "set-cookie":
            continue
        raw.append((key.encode("latin-1"), value.encode("latin-1")))
    for raw_cookie in resp.headers.get_list("set-cookie"):
        raw.append((b"set-cookie", raw_cookie.encode("latin-1")))
    if resp.content is not None and resp.status_code not in (204, 304):
        raw.append((b"content-length", str(len(resp.content)).encode("latin-1")))
    out.raw_headers = raw


async def _forward(cfg: ServerConfig, request: Request, path: str, client):
    url = "%s/%s" % (cfg.upstream, path)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_HEADERS}
    # 保留浏览器访问网关时的 Host，否则 HydroOJ 看到的 Host 是
    # 127.0.0.1:8889，与 Referer/Origin 不一致会触发 CsrfTokenError。
    original_host = request.headers.get("host", "")
    if original_host:
        headers["host"] = original_host
        headers["x-forwarded-host"] = original_host
    headers["x-forwarded-proto"] = request.url.scheme
    # HydroOJ 插件用 UA 判断"是否考试客户端"（ua.includes('exam-client')），
    # 而 WebView 的 UA 是 Edge，所以由网关统一补标，避免被门禁 403。
    if cfg.force_client_ua:
        headers["user-agent"] = cfg.force_client_ua
    body = await request.body()
    try:
        resp = await client.request(request.method, url, headers=headers,
                                    content=body,
                                    params=request.query_params.multi_items())
    except Exception as exc:
        return HTMLResponse(_page("无法连接 OJ",
                                  "后端服务不可达：%s: %s" % (type(exc).__name__, exc)),
                            status_code=502)
    out = Response(content=resp.content, status_code=resp.status_code)
    _copy_headers(resp, out)
    location = resp.headers.get("location")
    if location and location.startswith(cfg.upstream):
        out.headers["location"] = location[len(cfg.upstream):] or "/"
    return out


def _page(title: str, body: str) -> str:
    return ("<html><head><meta charset='utf-8'><title>%s</title>"
            "<style>body{font-family:system-ui;margin:60px auto;max-width:640px;"
            "line-height:1.8;color:#333}h1{font-size:20px}</style></head>"
            "<body><h1>%s</h1><div>%s</div></body></html>"
            % (html.escape(title), html.escape(title), body))


def _validate_runtime_config(cfg: ServerConfig) -> None:
    missing = []
    for attr in ("admin_password", "session_secret", "sign_key"):
        value = str(getattr(cfg, attr, "") or "")
        if not value or "CHANGE_ME" in value:
            missing.append(attr)
    if missing:
        raise SystemExit(
            "缺少生产密钥：%s\n"
            "请复制 server/.env.example 为 server/.env，生成随机值后重启。\n"
            "例如：python -c \"import secrets; print(secrets.token_urlsafe(32))\"" % ", ".join(missing)
        )


def main() -> None:
    import uvicorn
    cfg = ServerConfig()
    _validate_runtime_config(cfg)
    store = Store(cfg.db_path)
    print("考试网关启动: http://%s:%d  ->  上游 %s" % (cfg.host, cfg.port, cfg.upstream))
    uvicorn.run(build_app(cfg, store), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
