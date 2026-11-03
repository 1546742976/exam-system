"""长空御风考试客户端（重写版）入口。

与原有前端 static/ 完全兼容：js_api 只暴露下面这些方法，名字与返回结构不可改。

重要：JsApi 的所有内部状态都必须以 _ 开头。
pywebview 会把 js_api 对象的属性暴露给前端，一旦这里放了 Window /
CaptureService 之类的复杂对象，就会触发 .NET 属性递归遍历，控制台被
"Error while processing window.native.ActiveControl..." 刷屏。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from capture import CaptureService, enumerate_cameras, enumerate_monitors
from common import (Config, TeacherAuth, check_dependencies, check_host_requirements,
                    get_logger, load_config, missing_core)
from fingerprint import compute as compute_fingerprint
from local_server import ScreenshotStore, start_server
from monitors import MonitorService
from reporter import LoginError, Reporter
from uploader import Uploader

try:
    import webview
except Exception:  # pragma: no cover
    webview = None


class JsApi:
    """暴露给 window.pywebview.api 的方法集合（方法名不可改）。"""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._log = get_logger(cfg, "jsapi")
        self._window = None
        self._reporter = Reporter(cfg)
        self._capture = CaptureService(cfg)
        self._monitors = MonitorService(cfg, callback=self._on_alerts)
        self._fingerprint = compute_fingerprint()
        self._uploader = Uploader(cfg)
        self._capture.uploader = self._uploader
        self._logged_in = False
        self._log.info("客户端指纹: %s", self._fingerprint)

    # -- 内部 -------------------------------------------------------
    def _on_alerts(self, alerts: list) -> None:
        extra = {
            "health": self._capture.health() if self._logged_in else {},
            "uname": self._reporter.uname,
            "endpoint": self._reporter.endpoint,
            "hb_seq": self._monitors.round,
            "upload": self._uploader.stats(),
        }
        self._reporter.report(alerts, extra)

    # -- 前端调用的接口 ---------------------------------------------
    def login(self, uname, password, endpoint):
        try:
            if self._monitors._thread is not None:
                self._monitors.stop()
            self._reporter.login(uname, password, endpoint)
        except LoginError as exc:
            self._log.warning("登录失败: %s", exc)
            return {"error": str(exc)}
        except Exception as exc:
            self._log.exception("登录异常")
            return {"error": "登录失败: %s" % exc}
        self._capture.sid = uname
        self._logged_in = True
        if self._cfg.upload_enabled:
            if self._uploader.bind(self._reporter.session, self._reporter.endpoint,
                                   uname, self._fingerprint,
                                   preset_key=getattr(self._reporter, "upload_key", "")):
                self._capture.uploader_session = self._reporter.session
            else:
                self._logged_in = False
                return {"error": "上传会话或设备指纹校验失败，请联系监考老师"}
        self._monitors.start()
        return {"success": True}

    def getCookies(self):
        return self._reporter.cookies()

    def getAlerts(self):
        """返回 None 表示还没采样完，前端 1 秒后重试。"""
        latest = self._monitors.get_latest()
        if not latest and self._monitors.round == 0:
            return None
        return latest

    def getAvailableDevices(self):
        try:
            monitors = enumerate_monitors()
        except Exception as exc:
            self._log.error("枚举屏幕失败: %s", exc)
            monitors = {"1": "Display1"}
        try:
            cameras = enumerate_cameras()
        except Exception as exc:
            self._log.error("枚举摄像头失败: %s", exc)
            cameras = {}
        self._log.info("设备枚举：屏幕 %s 摄像头 %s", list(monitors), list(cameras))
        return {"monitors": monitors, "cameras": cameras}

    def startScreenRecorder(self, monitor_idx, monitor_name):
        ok = self._capture.start_screen(monitor_idx, monitor_name)
        self._log.info("startScreenRecorder(%s, %s) -> %s", monitor_idx, monitor_name, ok)
        return ok

    def startCameraRecorder(self, camera_idx, camera_name):
        ok = self._capture.start_camera(camera_idx, camera_name)
        self._log.info("startCameraRecorder(%s, %s) -> %s", camera_idx, camera_name, ok)
        return ok

    def captureHealth(self):
        """必须返回 bool：前端用它决定能不能点“下一步”。"""
        detail = self._capture.health()
        if not detail["ok"]:
            self._log.warning("health 未通过: %s", detail.get("problems"))
        return bool(detail["ok"])

    def getEndpoint(self):
        return self._reporter.endpoint

    def gotoExam(self):
        return self._goto_exam()

    # -- 额外接口（排障用，前端未调用） -----------------------------
    def healthDetail(self):
        return self._capture.health()

    def logout(self):
        self._monitors.stop()
        self._capture.stop_all()
        self._uploader.unbind()
        self._capture.uploader_session = None
        self._reporter.logout()
        self._logged_in = False
        return True

    # -- 内部：跳转到考试页 -----------------------------------------
    def _goto_exam(self):
        if self._window is None or not self._reporter.endpoint:
            return False
        exam_url = "http://%s%s" % (self._reporter.endpoint, self._cfg.exam_path)
        try:
            self._window.load_url(exam_url)
        except Exception as exc:
            self._log.error("打开考试页失败: %s", exc)
            return False
        opened = self._wait_loaded(self._reporter.endpoint, timeout=20)
        cookies = self._reporter.cookies()
        if not cookies:
            self._log.warning("没有可注入的 Cookie，可能是登录未成功")
        script = ";".join('document.cookie=%s'
                          % json.dumps("%s=%s; path=/" % (k, v))
                          for k, v in cookies.items())
        script += ";window.location.reload();"
        try:
            time.sleep(0.3)
            self._window.run_js(script)
            self._log.info("已注入 %d 个 Cookie 并刷新考试页", len(cookies))
        except Exception as exc:
            self._log.error("注入 Cookie 失败: %s", exc)
            return False
        return opened

    def _wait_loaded(self, host: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = self._window.get_current_url() or ""
            except Exception:
                url = ""
            if host in url:
                return True
            time.sleep(0.5)
        return False


def _port_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    while time.time() < deadline:
        sock = socket.socket()
        sock.settimeout(0.5)
        try:
            if sock.connect_ex((probe_host, port)) == 0:
                return True
        except Exception:
            pass
        finally:
            sock.close()
        time.sleep(0.3)
    return False


def _preflight(log) -> int:
    """依赖自检：缺什么一次性说清楚，不要抛 traceback。"""
    ver = sys.version_info
    if (ver.major, ver.minor) >= (3, 14):
        log.warning("当前 Python %d.%d：部分二进制轮子（av / opencv）可能没有 cp%d 版本。",
                    ver.major, ver.minor, ver.minor)
        log.warning("如果 pip 安装 av 或 opencv-python 失败，建议改用 Python 3.12 / 3.13。")
    host = check_host_requirements()
    fatal = [m for lvl, m in host if lvl == "error"]
    for lvl, msg in host:
        (log.error if lvl == "error" else log.warning)("%s", msg)
    if fatal and os.environ.get("CKYF_SKIP_HOST_CHECK") != "1":
        log.error("运行环境不满足要求，无法启动。")
        log.error("WebView2 下载：https://developer.microsoft.com/microsoft-edge/webview2/")
        return 5

    missing = check_dependencies()
    if not missing:
        return 0
    log.error("检测到 %d 个依赖缺失：", len(missing))
    for module, pip_name, level, why in missing:
        log.error("  [%s] %-14s -> %s", level, pip_name, why)
    names = " ".join(p for _, p, _, _ in missing)
    log.error("")
    log.error("请在客户端目录执行（建议先建虚拟环境）：")
    log.error("  python -m venv .venv")
    log.error("  PowerShell: .venv/Scripts/Activate.ps1")
    log.error("  cmd:        .venv/Scripts/activate.bat")
    log.error("  pip install -r requirements.txt")
    log.error("或只装缺失的： pip install %s", names)
    core = missing_core(missing)
    if core:
        log.error("")
        log.error("其中必需依赖缺失（%s），无法继续启动。",
                  ", ".join(m[1] for m in core))
        return 3
    log.warning("")
    log.warning("必需依赖齐全，可以继续启动（部分功能会降级）。")
    return 0


def main() -> int:
    cfg = load_config()
    log = get_logger(cfg, "main")
    log.info("=" * 60)
    log.info("长空御风考试客户端 v3 启动，Python %s", sys.version.split()[0])
    log.info("工作目录: %s", Path.cwd())


    code = _preflight(log)
    if code:
        return code

    auth = TeacherAuth(cfg)
    api = JsApi(cfg)
    shots = ScreenshotStore(cfg)
    shots.start()
    try:
        server, _ = start_server(cfg, api._capture, shots, auth)
    except Exception as exc:
        log.error("本地服务启动失败: %s", exc)
        return 4

    if not _port_ready(cfg.host, cfg.port, 20):
        log.error("本地服务未能在 %s:%s 上启动", cfg.host, cfg.port)
        return 2
    log.info("本地服务就绪: http://127.0.0.1:%s/", cfg.port)

    if webview is None:
        log.error("缺少 pywebview，无法创建窗口；可先用浏览器打开 http://127.0.0.1:%s/", cfg.port)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    window = webview.create_window(
        "长空御风考试客户端",
        "http://localhost:%s/" % cfg.port,
        js_api=api, width=1280, height=800, min_size=(1024, 700))
    api._window = window

    def _watchdog():
        """防止学生把窗口导航到别处：非本机、非考试服务器就拉回考试页。"""
        if not cfg.inject_url_redirect:
            return
        while True:
            time.sleep(10)
            try:
                if not api._logged_in:
                    continue
                url = (window.get_current_url() or "")
                from urllib.parse import urlsplit
                host_ok = urlsplit(url).netloc in {"localhost:%s" % cfg.port, "127.0.0.1:%s" % cfg.port, api._reporter.endpoint}
                if url and not host_ok:
                    log.warning("检测到窗口跳转: %s，强制拉回考试页", url)
                    api.gotoExam()
            except Exception:
                pass

    threading.Thread(target=_watchdog, name="url-watchdog", daemon=True).start()
    try:
        webview.start(debug=False)
    finally:
        api._monitors.stop()
        api._capture.stop_all()
        api._uploader.close()
        api._reporter.logout()
        server.should_exit = True
        shots.stop()
        log.info("客户端退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
