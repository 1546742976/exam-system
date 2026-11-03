"""本地 HTTP 服务（默认 0.0.0.0:34519）。

供两拨人用：
  * 本机前端（webview）——环回地址免鉴权；
  * 教师端远程——必须带 teacher_jwt（RS256/HS256）。
"""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import threading
import time
from pathlib import Path

from common import Config, TeacherAuth, get_logger, media_root

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                                   PlainTextResponse, Response, StreamingResponse)
    from fastapi.staticfiles import StaticFiles
except Exception:  # pragma: no cover
    FastAPI = None

M3U8_TYPE = "application/vnd.apple.mpegurl"
TS_TYPE = "video/mp2t"

EXEMPT_PREFIXES = ("/assets/", "/logo", "/index.html", "/favicon", "/static/")


class ScreenshotStore:
    """后台线程抓主屏 PNG，供 /screenshot/latest 使用。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "screenshot")
        self.png = b""
        self.updated_at = 0.0
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="screenshot", daemon=True).start()

    def _run(self) -> None:
        try:
            import mss
            from PIL import Image
        except Exception as exc:
            self.error = "缺少 mss/Pillow: %s" % exc
            self.log.error(self.error)
            return
        import io
        self.log.info("截图线程启动，间隔 %.1fs", self.cfg.screenshot_interval)
        while not self._stop.is_set():
            try:
                with mss.mss() as sct:
                    shot = sct.grab(sct.monitors[1])
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                with self._lock:
                    self.png = buf.getvalue()
                    self.updated_at = time.time()
            except Exception as exc:
                self.error = str(exc)
                self.log.warning("截图失败: %s", exc)
            self._stop.wait(self.cfg.screenshot_interval)

    def latest(self):
        with self._lock:
            return self.png

    def stop(self) -> None:
        self._stop.set()


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except Exception:
        return False


def _client_ip(request) -> str:
    """只信任 TCP 对端地址。

    学生机上的 34519 端口直接暴露给考场局域网，不能信任客户端自带的
    X-Forwarded-For；否则远程请求伪造 127.0.0.1 即可绕过教师 JWT。
    """
    return request.client.host if request.client else ""


def _extract_token(request) -> tuple:
    token = request.cookies.get("teacher_jwt")
    if token:
        return token, "cookie"
    for header in ("x-teacher-jwt", "X-Teacher-JWT"):
        token = request.headers.get(header)
        if token:
            return token, "header"
    for key in ("teacher_jwt", "token"):
        token = request.query_params.get(key)
        if token:
            return token, "query"
    return "", ""


def build_app(cfg: Config, service, shots: ScreenshotStore, auth: TeacherAuth):
    if FastAPI is None:
        raise RuntimeError("缺少 fastapi，请先 pip install -r requirements.txt")
    log = get_logger(cfg, "server")
    root = media_root(cfg)
    app = FastAPI(title="ckyf exam client", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins if hasattr(cfg, "cors_origins") else ["*"],
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path or "/"
        if path == "/" or any(path.startswith(p) for p in EXEMPT_PREFIXES) \
                or path.startswith("/static"):
            return await call_next(request)
        ip = _client_ip(request)
        if _is_loopback(ip):
            request.state.teacher = {"local": True}
            log.debug("Bypassing JWT for local request from %s to %s", ip, path)
            return await call_next(request)
        token, source = _extract_token(request)
        if not token:
            return JSONResponse({"detail": "缺少 teacher_jwt（cookie/header/query 均可）"},
                                status_code=401)
        try:
            payload = auth.verify(token)
        except Exception as exc:
            log.warning("令牌校验失败 %s (%s): %s", path, source, exc)
            return JSONResponse({"detail": "无效 token: %s" % exc}, status_code=401)
        request.state.teacher = payload
        log.info("JWT 校验通过 %s sub=%s (%s)", path, payload.get("sub"), source)
        return await call_next(request)

    @app.get("/screenshot/latest")
    def screenshot_latest():
        png = shots.latest()
        if not png:
            return Response(status_code=204)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/screenshot")
    def screenshot_alias():
        return screenshot_latest()

    @app.get("/recorder/list")
    def recorder_list():
        return JSONResponse({"names": service.names(), "health": service.health()})

    @app.get("/recorder/record_list")
    def record_list():
        items = []
        for d in sorted(root.glob("Recorder.*")):
            if (d / "video.m3u8").exists():
                items.append({"name": d.name[len("Recorder."):],
                              "path": d.name,
                              "mtime": int(d.stat().st_mtime)})
        return JSONResponse({"items": items})

    @app.get("/debug/state")
    def debug_state():
        try:
            import av
            av_version = getattr(av, "__version__", "?")
        except Exception:
            av_version = "未安装"
        return JSONResponse({
            "config": {
                "port": cfg.port, "fps": cfg.fps, "muxer": cfg.muxer,
                "segment_seconds": cfg.segment_seconds,
                "encoders": cfg.encoders, "enabled_monitors": cfg.enabled_monitors,
            },
            "av_version": av_version,
            "screenshot": {"has_image": bool(shots.latest()),
                            "error": shots.error},
            "health": service.health(),
            "recorders": service.debug_state(),
        })

    @app.get("/recorder/live/{name}.m3u8")
    def live(name: str):
        rec = service.get(name)
        if rec is None:
            log.warning("请求了不存在的录制器: %s（已有: %s）", name, service.names())
            return JSONResponse({"detail": "recorder not found", "name": name,
                                 "available": service.names()}, status_code=404)
        # 关键：播放器第一次请求时切片往往还没产出，这里等一小会儿再回，
        # 避免返回 503 让 hls.js 进入错误重试（表现为"第一次预览黑屏"）。
        wait = float(getattr(cfg, "preview_wait_seconds", 0) or 0)
        if wait > 0 and not rec.get_latest_segments():
            rec.wait_first_segment(wait)
        segs = rec.get_latest_segments()
        if not segs:
            log.warning("直播列表为空: %s（error=%s）", name, rec.last_error or "-")
            return PlainTextResponse(rec.generate_live_m3u8(), media_type=M3U8_TYPE,
                                     status_code=503,
                                     headers={"X-Recorder-Error": rec.last_error or "no segments"})
        return PlainTextResponse(rec.generate_live_m3u8(), media_type=M3U8_TYPE,
                                 headers={"Cache-Control": "no-store"})

    @app.get("/recorder/file/{path:path}")
    def media_file(path: str, request: Request):
        target = None
        for candidate in ((root / path), (root / ("Recorder." + path))):
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root.resolve())
            except Exception:
                continue
            if resolved.is_file():
                target = resolved
                break
        if target is None:
            log.warning("媒体文件不存在或路径非法: %s", path)
            return JSONResponse({"detail": "not found"}, status_code=404)
        return _range_response(target, request, log)

    # ------------------------------------------------------------ 抽查帧
    @app.get("/probe/frame")
    def probe_frame(nonce: str = ""):
        """服务端随机抽查：现场抓一帧并叠上随机码，防预录/防静态图。"""
        import io
        try:
            import mss
            from PIL import Image, ImageDraw
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            draw = ImageDraw.Draw(img)
            text = "CKyF-CHALLENGE %s %s" % (nonce or "-", time.strftime("%Y-%m-%d %H:%M:%S"))
            draw.rectangle([0, 0, min(img.width, 620), 36], fill=(0, 0, 0))
            draw.text((10, 11), text, fill=(0, 255, 0))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return Response(content=buf.getvalue(), media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})
        except Exception as exc:
            log.warning("抽查帧失败: %s", exc)
            return JSONResponse({"detail": "抽查帧失败: %s" % exc}, status_code=500)

    # ------------------------------------------------------------ 静态前端
    static_dir = Path(cfg.static_dir)
    if not static_dir.is_absolute():
        static_dir = Path(__file__).resolve().parent / static_dir
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
        log.info("静态目录已挂载: %s", static_dir)
    else:
        log.warning("未找到静态目录，界面不可用: %s", static_dir)

        @app.get("/")
        def fallback():
            return HTMLResponse("<h1>static 目录缺失</h1><p>请把前端 static 放到客户端目录下。</p>",
                                status_code=500)

    return app


def _range_response(path: Path, request, log):
    """支持 HTTP Range 的文件下载（录像回放要拖进度条）。"""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if path.suffix == ".m3u8":
        ctype = M3U8_TYPE
    elif path.suffix == ".ts":
        ctype = TS_TYPE
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(str(path), media_type=ctype,
                            headers={"Accept-Ranges": "bytes"})
    try:
        spec = range_header.replace("bytes=", "").split(",")[0]
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    except Exception:
        log.warning("Range 头无法解析: %s", range_header)
        return Response(status_code=416)
    start = max(0, start)
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416,
                        headers={"Content-Range": "bytes */%d" % size})
    length = end - start + 1

    def iterator():
        with open(path, "rb") as fh:
            fh.seek(start)
            remain = length
            while remain > 0:
                chunk = fh.read(min(256 * 1024, remain))
                if not chunk:
                    break
                remain -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": "bytes %d-%d/%d" % (start, end, size),
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(iterator(), status_code=206, media_type=ctype, headers=headers)


def start_server(cfg: Config, service, shots: ScreenshotStore, auth: TeacherAuth):
    """在后台线程里跑 uvicorn。"""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "缺少 uvicorn，本地服务无法启动。请先安装依赖: pip install -r requirements.txt"
        ) from exc
    app = build_app(cfg, service, shots, auth)
    config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning",
                            access_log=True)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="local-server", daemon=True)
    thread.start()
    return server, thread
