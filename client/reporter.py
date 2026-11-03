"""与服务端（考试网关）通信：登录、告警上报、会话 Cookie 导出。"""
from __future__ import annotations

import json
import time

from common import Config, get_logger

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

UA = "exam-client/2.0"


class LoginError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Reporter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "reporter")
        self.endpoint = ""
        self.uname = ""
        self.session = None
        self.alert_total = 0
        self.upload_key = ""
        self.upload_url = ""
        self.last_report_at = 0.0

    # -- 登录 -------------------------------------------------------
    def login(self, uname: str, password: str, endpoint: str) -> bool:
        if requests is None:
            raise LoginError("缺少 requests 库")
        endpoint = (endpoint or "").strip()
        for prefix in ("http://", "https://"):
            if endpoint.startswith(prefix):
                endpoint = endpoint[len(prefix):]
        endpoint = endpoint.rstrip("/")
        if not endpoint:
            raise LoginError("接口地址为空")
        url = "http://%s/login" % endpoint
        session = requests.Session()
        session.trust_env = False
        try:
            resp = session.post(
                url,
                data={"uname": uname, "password": password},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                    "User-Agent": UA,
                },
                allow_redirects=False,
                timeout=15)
        except Exception as exc:
            raise LoginError("无法连接服务器：%s" % exc)
        if resp.status_code in (301, 302, 303):
            self.endpoint, self.uname, self.session = endpoint, uname, session
            # 网关会在登录响应头里直接下发上传密钥，省掉一次 /exam/session 往返
            self.upload_key = resp.headers.get("x-exam-upload-key", "") or ""
            self.upload_url = resp.headers.get("x-exam-upload-url", "/exam/upload") or ""
            self.log.info("登录成功：%s@%s", uname, endpoint)
            return True
        if resp.status_code == 401:
            raise LoginError("密码错误", 401)
        if resp.status_code == 404:
            raise LoginError("用户不存在", 404)
        # 200 一般是登录页回显（密码错 / 需要验证码 / 被风控）
        raise LoginError("登录失败: HTTP %d" % resp.status_code, resp.status_code)

    # -- 会话 -------------------------------------------------------
    def cookies(self) -> dict:
        if self.session is None:
            return {}
        try:
            return dict(self.session.cookies.get_dict())
        except Exception:
            return {}

    def cookie_string(self) -> str:
        return "; ".join("%s=%s" % (k, v) for k, v in self.cookies().items())

    # -- 上报 -------------------------------------------------------
    def report(self, alerts: list, extra: dict = None) -> bool:
        if self.session is None or not self.endpoint:
            return False
        # HydroOJ addon 的 /exam/alert 用 @param('alerts', Types.Content) 接字符串再 JSON.parse，
        # 所以这里把数组再编码一层字符串；同时保留 alerts_list 供自定义网关使用。
        alert_list = alerts or []
        payload = {"alerts": json.dumps(alert_list, ensure_ascii=False),
                   "alerts_list": alert_list,
                   "client_time": int(time.time())}
        if extra:
            payload.update(extra)
        try:
            resp = self.session.post(
                "http://%s/exam/alert" % self.endpoint,
                data=json.dumps(payload, ensure_ascii=False),
                headers={"Content-Type": "application/json;charset=utf-8",
                         "Accept": "application/json",
                         "User-Agent": UA},
                timeout=10)
            resp.raise_for_status()
            self.alert_total += len(alerts or [])
            self.last_report_at = time.time()
            return True
        except Exception as exc:
            self.log.warning("告警上报失败: %s", exc)
            return False

    def logout(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
        self.session = None
        self.endpoint = ""
        self.uname = ""
