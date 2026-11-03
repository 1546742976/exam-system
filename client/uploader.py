"""边录边传：把切片实时推到网关存档。

意义（防逆向的关键）：
  * 证据在服务器，密钥在服务器 —— 学生删掉本地录像也没用；
  * 序号连续，缺一片服务端就知道；
  * 签名使用会话密钥，可检测传输篡改；客户端持有密钥，不能据此证明录像真实。
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common import Config, get_logger

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

UA = "exam-client/2.0"
MAX_SEGMENT_BYTES = 8 * 1024 * 1024


class Uploader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "uploader")
        self.enabled = False
        self.endpoint = ""
        self.uname = ""
        self.key = ""
        self.uploaded = 0
        self.frames = 0
        self.failed = 0
        self._seq = 0
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(8)
        self._generation = 0
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upload")

    # -- 绑定会话 ---------------------------------------------------
    def bind(self, session, endpoint: str, uname: str, fp: str = "",
             preset_key: str = "") -> bool:
        """绑定上传通道：优先用登录响应头下发的密钥（省一次往返），否则回退请求 /exam/session。"""
        if requests is None or session is None or not endpoint:
            return False
        self.unbind()
        if preset_key and not fp:
            self.key = preset_key
            self.uname = uname
            self.endpoint = endpoint
            self.enabled = True
            self.fingerprint_ok = True
            self.log.info("边录边传已启用（登录响应头下发密钥），uname=%s", uname)
            return True
        try:
            resp = session.get("http://%s/exam/session" % endpoint,
                               params={"fp": fp} if fp else None,
                               headers={"User-Agent": UA}, timeout=10)
            if resp.status_code != 200:
                self.log.warning("申请上传密钥失败: HTTP %d", resp.status_code)
                return False
            data = resp.json()
        except Exception as exc:
            self.log.warning("申请上传密钥异常: %s", exc)
            return False
        self.fingerprint_ok = data.get("fingerprint_ok", True)
        self.key = data.get("upload_key") or ""
        self.uname = data.get("uname") or uname
        self.endpoint = endpoint
        self.enabled = bool(self.key)
        self.log.info("边录边传已启用，uname=%s 指纹校验=%s",
                      self.uname, "通过" if self.fingerprint_ok else "不匹配")
        return self.enabled

    def unbind(self) -> None:
        self._generation += 1
        self.enabled = False
        self.key = ""
        self.endpoint = ""

    def close(self):
        self.unbind()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def _submit(self, fn, *args):
        if not self._slots.acquire(blocking=False):
            self.failed += 1
            self.log.warning("上传队列已满，跳过本次上传；本地录像保留")
            return
        generation = self._generation
        def run():
            if generation == self._generation and self.enabled:
                fn(*args)
        try:
            future = self._pool.submit(run)
            future.add_done_callback(lambda _: self._slots.release())
        except RuntimeError:
            self._slots.release()

    def stats(self) -> dict:
        return {"enabled": self.enabled, "segments": self.uploaded,
                "frames": self.frames, "failed": self.failed,
                "mode": getattr(self.cfg, "upload_mode", "segments")}

    # -- 提交 -------------------------------------------------------
    def submit(self, session, rec_key: str, path, num: int) -> None:
        if not self.enabled or getattr(self.cfg, "upload_mode", "segments") != "segments":
            return
        try:
            if path.stat().st_size > MAX_SEGMENT_BYTES:
                self.log.warning("切片过大，跳过上传: %s", path.name)
                return
        except Exception:
            return
        self._submit(self._do_upload, session, rec_key, path, num)

    def submit_frame(self, session, rec_key: str, jpeg: bytes, ts: int) -> None:
        """上传一张关键帧（JPEG）。"""
        if not self.enabled:
            return
        self._submit(self._do_frame, session, rec_key, jpeg, ts)

    def _post(self, session, url, **kwargs):
        # Retry transient failures once with the same sequence/body/signature.
        for attempt in range(2):
            try:
                response = session.post(url, **kwargs)
                if response.status_code not in (502, 503, 504) or attempt:
                    return response
            except requests.RequestException:
                if attempt:
                    raise
            time.sleep(0.5)

    def _do_frame(self, session, rec_key: str, jpeg: bytes, ts: int) -> None:
        seq = self._next_seq()
        mac = hmac.new(self.key.encode("utf-8"), digestmod=hashlib.sha256)
        mac.update(("%s|%d|" % (self.uname, seq)).encode("utf-8"))
        mac.update(jpeg)
        name = "%s_%d.jpg" % (rec_key, ts)
        url = "http://%s/exam/upload?seq=%d&kind=frame&name=%s" % (self.endpoint, seq, name)
        try:
            resp = self._post(session, url, data=jpeg, timeout=10, headers={
                "Content-Type": "application/octet-stream",
                "User-Agent": UA,
                "X-Exam-Sig": mac.hexdigest(),
            })
            if resp.status_code == 200:
                self.frames += 1
            else:
                self.failed += 1
        except Exception as exc:
            self.failed += 1
            self.log.debug("关键帧上传失败: %s", exc)

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _do_upload(self, session, rec_key: str, path, num: int) -> None:
        try:
            body = path.read_bytes()
        except Exception as exc:
            self.log.warning("读取切片失败 %s: %s", path, exc)
            return
        seq = self._next_seq()
        mac = hmac.new(self.key.encode("utf-8"), digestmod=hashlib.sha256)
        mac.update(("%s|%d|" % (self.uname, seq)).encode("utf-8"))
        mac.update(body)
        name = "%s_%d.ts" % (rec_key, num)
        url = "http://%s/exam/upload?seq=%d&kind=ts&name=%s" % (self.endpoint, seq, name)
        try:
            resp = self._post(session, url, data=body, timeout=15, headers={
                "Content-Type": "application/octet-stream",
                "User-Agent": UA,
                "X-Exam-Sig": mac.hexdigest(),
            })
            if resp.status_code == 200:
                self.uploaded += 1
                self.log.debug("已上传 %s (seq=%d, %d 字节)", name, seq, len(body))
            else:
                self.failed += 1
                self.log.warning("上传被拒 %s: HTTP %d", name, resp.status_code)
        except Exception as exc:
            self.failed += 1
            self.log.warning("上传失败 %s: %s", name, exc)
