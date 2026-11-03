"""屏幕/摄像头采集 + HLS 录制（PyAV 16 修复版）。

相对原版的关键修复：
1. 显式设置 codec_context.time_base 与逐帧 pts/time_base（原版报
   "'stream_local' object has no attribute 'time_base'" 的根因）；
2. stream.encode() 返回 packet 列表，逐包 mux；结束时 encode(None) flush；
3. 像素格式统一交给 VideoFrame.reformat()，不再手搓 BGR2YUV_I420；
4. 编码器按 nvenc -> qsv -> amf -> libx264 逐个回退；
5. 切片签名改为 HMAC-SHA256（原版是固定盐 SHA1）；
6. 看门狗把失败原因记录下来，供 health()/教师端查看。
"""
from __future__ import annotations

import platform
import threading
import time
from collections import deque
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from common import Config, get_logger, media_root, sign_segment

logger = get_logger(Config(), "capture")

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import mss
except Exception:  # pragma: no cover
    mss = None

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import av
except Exception:  # pragma: no cover
    av = None


def cam_backend() -> int:
    """按平台选 OpenCV 采集后端：Win=DirectShow / mac=AVFoundation / Linux=V4L2。"""
    if cv2 is None:
        return 0
    system = platform.system().lower()
    if system == "windows":
        return getattr(cv2, "CAP_DSHOW", 0)
    if system == "darwin":
        return getattr(cv2, "CAP_AVFOUNDATION", 0)
    return getattr(cv2, "CAP_V4L2", 0)


def device_key(name, idx) -> str:
    """与前端 static 包里的命名规则保持一致：只把非 ASCII 字符换成 _，
    若发生过替换则追加 _<idx>。不要做其它清洗，否则前端拼出来的
    /recorder/live/<name>.m3u8 会对不上（含空格、括号都要原样保留）。"""
    ascii_name = "".join(c if ord(c) < 128 else "_" for c in str(name))
    key = ascii_name if ascii_name == name else ascii_name + "_" + str(idx)
    return key or ("device_" + str(idx))


def safe_dirname(key: str) -> str:
    """仅用于磁盘目录名：把 Windows 不允许的字符换掉。"""
    bad = set('<>:"/|?*') | {chr(92)}  # Windows 非法字符 + 反斜杠
    out = "".join("_" if (c in bad or ord(c) < 32) else c for c in key)
    return out.strip().rstrip(".") or "device"


# ---------------------------------------------------------------- 设备枚举

def enumerate_monitors() -> dict:
    """返回 {idx: 可读名称}，idx 与 mss.monitors 下标一致（从 1 开始）。"""
    result = {}
    if mss is None:
        return {"1": "Primary"}
    try:
        with mss.mss() as sct:
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue
                name = "Display%d (%dx%d)" % (i, mon.get("width", 0), mon.get("height", 0))
                try:
                    import wmi
                    c = wmi.WMI()
                    ents = c.Win32_PnPEntity(Caption="Generic PnP Monitor")
                    if len(ents) >= i:
                        cap = getattr(ents[i - 1], "Caption", "") or ""
                        if cap:
                            name = "Display%d [%s]" % (i, cap)
                except Exception:
                    pass
                result[str(i)] = name
    except Exception as exc:
        logger.warning("枚举屏幕失败: %s", exc)
        result.setdefault("1", "Display1")
    return result


def enumerate_cameras() -> dict:
    """返回 {idx: 名称}。优先 pygrabber(DirectShow)，失败则用 OpenCV 暴搜。"""
    names = []
    if platform.system().lower() == "windows":
        try:
            from pygrabber.dshow_graph import FilterGraph
            names = list(FilterGraph().get_input_devices())
        except Exception as exc:
            logger.debug("pygrabber 枚举失败: %s", exc)
    if names:
        return {str(i): n for i, n in enumerate(names)}
    found = {}
    if cv2 is not None:
        for i in range(6):
            cap = None
            try:
                cap = cv2.VideoCapture(i, cam_backend())
                if cap.isOpened():
                    found[str(i)] = "Camera %d" % i
            except Exception:
                pass
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
    return found


# ---------------------------------------------------------------- 采集器

class BaseCapture:
    def __init__(self, name: str, idx: int, fps: int):
        self.name = name
        self.idx = int(idx)
        self.fps = int(fps)
        self.width = 0
        self.height = 0

    def capture_frame(self):
        raise NotImplementedError

    def stop(self):
        pass


class MssScreenCapture(BaseCapture):
    """用 mss 抓屏。

    注意：mss 的 GDI 上下文是**线程私有**的，必须在真正抓帧的那个线程里
    创建实例，否则会报 "Windows graphics function failed: BitBlt: 找不到指定的模块"。
    这里用 threading.local 做到「谁调用谁创建」。
    """

    def __init__(self, name: str, idx: int, fps: int):
        super().__init__(name, idx, fps)
        if mss is None:
            raise RuntimeError("mss 未安装")
        self._local = threading.local()
        probe = mss.mss()
        try:
            mons = probe.monitors
            if self.idx < 1 or self.idx >= len(mons):
                self.idx = 1
            self._mon = dict(mons[self.idx])
            self.width = self._mon.get("width", 0)
            self.height = self._mon.get("height", 0)
        finally:
            try:
                probe.close()
            except Exception:
                pass

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
        return sct

    def capture_frame(self):
        try:
            shot = self._sct().grab(self._mon)
        except Exception:
            # 句柄失效就重建一次
            try:
                self._local.sct.close()
            except Exception:
                pass
            self._local.sct = mss.mss()
            shot = self._local.sct.grab(self._mon)
        img = np.array(shot, dtype=np.uint8)
        if cv2 is not None:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img[:, :, :3]

    def stop(self):
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._local.sct = None


class CameraCapture(BaseCapture):
    def __init__(self, name: str, idx: int, fps: int):
        super().__init__(name, idx, fps)
        if cv2 is None:
            raise RuntimeError("opencv-python 未安装")
        try:
            self._cap = cv2.VideoCapture(self.idx, cam_backend())
        except Exception:
            self._cap = cv2.VideoCapture(self.idx)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self.idx)
        if not self._cap.isOpened():
            raise RuntimeError("无法打开摄像头索引 " + str(self.idx))
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if self.width <= 0 or self.height <= 0:
            self.width, self.height = 640, 480

    def capture_frame(self):
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise RuntimeError("无法从摄像头读取帧")
        return frame

    def stop(self):
        try:
            if self._cap is not None and self._cap.isOpened():
                self._cap.release()
        except Exception:
            pass


def create_screen_capture(name: str, idx: int, fps: int) -> BaseCapture:
    return MssScreenCapture(name, idx, fps)


def create_camera_capture(name: str, idx: int, fps: int) -> BaseCapture:
    return CameraCapture(name, idx, fps)


# ---------------------------------------------------------------- 录制器

ENCODER_OPTIONS = {
    # 只保留各编码器最通用、跨版本都认的参数，避免 avcodec_open2 报 22
    "nvenc": {"preset": "p4"},
    "qsv": {"preset": "faster"},
    "amf": {"quality": "speed"},
    "libx264": {"preset": "ultrafast", "tune": "zerolatency", "crf": "28"},
    "libx265": {"preset": "ultrafast"},
}


def _encoder_options(encoder: str) -> dict:
    for key, opts in ENCODER_OPTIONS.items():
        if key in encoder:
            return dict(opts)
    return {"preset": "ultrafast"}


def _available_encoders(preferred) -> list:
    if av is None:
        return []
    out = []
    for enc in preferred:
        if enc in av.codecs_available:
            out.append(enc)
    if "libx264" not in out and "libx264" in av.codecs_available:
        out.append("libx264")
    return out


class Recorder:
    """把一路采集写成「3 秒一个 .ts」+ 签名 + 上传。

    muxer=segment（默认）：自己切段，每 segment_seconds 关一个容器再开下一个。
        好处：切片什么时候产生完全可控，不依赖 FFmpeg 的 hls muxer 行为。
    muxer=hls：交给 FFmpeg 的 hls muxer（兼容旧行为，不推荐）。
    """

    def __init__(self, capture: BaseCapture, key: str, sid: str, cfg: Config,
                 on_segment=None, on_keyframe=None, kind: str = "screen"):
        self.capture = capture
        self.key = key
        self.sid = sid or "unknown"
        self.cfg = cfg
        self.output_dir = media_root(cfg) / ("Recorder." + safe_dirname(key))
        self.recording = False
        self.last_error = ""
        self.encoder = ""
        self.segment_count = 0
        self.muxer = (getattr(cfg, "muxer", "segment") or "segment").lower()
        self._latest = deque(maxlen=3)
        self._seg_lock = threading.Lock()
        self._frame_index = 0
        self._packets = 0
        self._seg_no = 0
        self._seg_frames = 0
        self._seg_target = max(1, int(cfg.fps) * max(1, int(cfg.segment_seconds)))
        self._last_progress = time.time()
        self._cost_ms = 0.0
        self._last_seg_seconds = 0.0
        self._bytes_total = 0
        self._kf_last = 0.0
        self._on_keyframe = on_keyframe
        self._tb = Fraction(1, max(1, int(cfg.fps)))
        self.out_w, self.out_h = self._target_size()
        self._container = None
        self._stream = None
        self._stop = threading.Event()
        self._threads = []
        self._on_segment = on_segment
        self.kind = kind or "screen"
        self._log = get_logger(cfg, "recorder")

    def _target_size(self):
        """按 capture_scale 缩小的输出尺寸（yuv420p 要求偶数）。"""
        scale = float(getattr(self.cfg, "capture_scale", 1.0) or 1.0)
        scale = min(1.0, max(0.1, scale))
        src_w = int(self.capture.width) or 1280      # 采集器没报尺寸时兜底
        src_h = int(self.capture.height) or 720
        w = int(src_w * scale) // 2 * 2
        h = int(src_h * scale) // 2 * 2
        return max(2, w), max(2, h)

    # -- 对外 -------------------------------------------------------
    def start(self) -> bool:
        if self.recording:
            return True
        if av is None:
            self.last_error = "PyAV 未安装"
            return False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segment_count = 0
        self._frame_index = 0
        self._packets = 0
        self._seg_frames = 0
        self._seg_no = 0
        self._stop.clear()
        self._last_progress = time.time()
        if not self._pick_encoder():
            return False
        if self.muxer == "hls":
            if not self._open_hls_container():
                return False
        elif not self._open_next_segment():
            return False
        self.recording = True
        self._threads = [
            threading.Thread(target=self._record_loop, name="rec-" + self.key, daemon=True),
            threading.Thread(target=self._watchdog, name="wd-" + self.key, daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._log.info("Recorder.%s 开始录制 编码器=%s 采集=%dx%d 输出=%dx%d fps=%d "
                       "码率=%dkbps 切片=%ds muxer=%s PyAV=%s",
                       self.key, self.encoder, self.capture.width, self.capture.height,
                       self.out_w, self.out_h, self.cfg.fps, self._bitrate_kbps(),
                       self.cfg.segment_seconds, self.muxer,
                       getattr(av, "__version__", "?"))
        return True

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=5.0)
        self._threads = []
        if self.muxer == "segment":
            self._close_segment()
        else:
            self._flush_hls()
        if self.recording:
            self._log.info("Recorder.%s 已停止，共 %d 片", self.key, self.segment_count)
        self.recording = False
        try:
            self.capture.stop()
        except Exception:
            pass

    def wait_first_segment(self, timeout: float) -> bool:
        """阻塞等待第一个切片落盘（前端预览要靠它，否则播放器拿到空列表）。"""
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if self.get_latest_segments() or not self.recording:
                return bool(self.get_latest_segments())
            time.sleep(0.1)
        return bool(self.get_latest_segments())

    def get_latest_segments(self) -> list:
        with self._seg_lock:
            return list(self._latest)

    def generate_live_m3u8(self) -> str:
        segs, last = [], -1
        for n in self.get_latest_segments():
            if isinstance(n, int) and n > last:
                segs.append(n)
                last = n
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:%d"
                 % max(1, int(self.cfg.segment_seconds))]
        if segs:
            lines.append("#EXT-X-MEDIA-SEQUENCE:%d" % segs[0])
        for s in segs:
            lines.append("#EXTINF:%d.0," % max(1, int(self.cfg.segment_seconds)))
            lines.append("/recorder/file/%s/video_%d.ts" % (quote(str(self.key), safe=""), s))
        return "\n".join(lines) + "\n"

    # -- 编码器选择 -------------------------------------------------
    def _pick_encoder(self) -> bool:
        last = ""
        for enc in _available_encoders(self.cfg.encoders):
            if self._probe_encoder(enc):
                self.encoder = enc
                return True
            last = enc
        self.last_error = "没有可用编码器（最后尝试: %s）" % (last or "-")
        self._log.error(self.last_error)
        return False

    def _bitrate_kbps(self) -> int:
        cfg = self.cfg
        if self.kind == "camera":
            v = int(getattr(cfg, "camera_bitrate_kbps", 0) or 0)
        else:
            v = int(getattr(cfg, "screen_bitrate_kbps", 0) or 0)
        if v <= 0:
            v = int(getattr(cfg, "bitrate_kbps", 0) or 0)
        return v

    def _configure(self, stream, width: int, height: int, encoder: str = "") -> None:
        width, height = self.out_w, self.out_h
        """PyAV 16：优先配置 codec_context，兼容旧版本时退回 stream。"""
        cc = getattr(stream, "codec_context", None)
        for target in [t for t in (cc, stream) if t is not None]:
            for attr, value in (("width", width), ("height", height),
                                ("pix_fmt", "yuv420p"),
                                ("time_base", self._tb),
                                ("framerate", Fraction(int(self.cfg.fps), 1))):
                try:
                    setattr(target, attr, value)
                except Exception:
                    pass
        if cc is not None:
            try:
                cc.options = _encoder_options(encoder or self.encoder)
            except Exception:
                pass
            kbps = self._bitrate_kbps()
            if kbps > 0:
                try:
                    cc.bit_rate = kbps * 1000
                except Exception:
                    pass

    def _probe_encoder(self, encoder: str) -> bool:
        """浅检参数 + 干跑 12 帧，确认「真能写出字节」。"""
        try:
            ctx = av.CodecContext.create(encoder, "w")
        except Exception as exc:
            self._log.warning("编码器 %s 不存在: %s", encoder, exc)
            return False
        try:
            for attr, value in (("width", self.out_w),
                                ("height", self.out_h),
                                ("pix_fmt", "yuv420p"),
                                ("time_base", self._tb),
                                ("framerate", Fraction(int(self.cfg.fps), 1))):
                try:
                    setattr(ctx, attr, value)
                except Exception:
                    pass
            try:
                ctx.options = _encoder_options(encoder)
                ctx.open()
            except AttributeError:
                pass
        except Exception as exc:
            self._log.warning("编码器 %s 参数不被接受: %s", encoder, exc)
            return False
        finally:
            try:
                ctx.close()
            except Exception:
                pass

        ok, detail = self._dry_run(encoder)
        if ok:
            self._log.info("编码器自检通过: %s（%s）", encoder, detail)
        else:
            self._log.warning("编码器 %s 干跑失败: %s", encoder, detail)
        return ok

    def _dry_run(self, encoder: str):
        """写 12 帧到临时 mpegts 文件，验证采集+编码+封装整条链路。"""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="ckyf_probe_"))
        target = tmp / "p.ts"
        try:
            cont = av.open(str(target), mode="w", format="mpegts")
            st = cont.add_stream(encoder, rate=int(self.cfg.fps))
            self._configure(st, self.capture.width, self.capture.height, encoder)
            blank = np.zeros((self.out_h, self.out_w, 3), dtype=np.uint8)
            packets = 0
            for i in range(12):
                fr = av.VideoFrame.from_ndarray(blank, format="bgr24")
                try:
                    fr = fr.reformat(format="yuv420p")
                except Exception:
                    pass
                fr.pts = i
                fr.time_base = self._tb
                for pkt in st.encode(fr):
                    cont.mux(pkt)
                    packets += 1
            for pkt in st.encode(None):
                cont.mux(pkt)
                packets += 1
            cont.close()
            size = target.stat().st_size if target.exists() else 0
            if size > 0:
                return True, "%d 包 / %d 字节" % (packets, size)
            return False, "%d 包但文件为空" % packets
        except Exception as exc:
            return False, str(exc)[:160]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- 容器管理 ---------------------------------------------------
    def _open_next_segment(self) -> bool:
        path = self.output_dir / ("video_%d.ts" % self._seg_no)
        try:
            self._container = av.open(str(path), mode="w", format="mpegts")
            stream = self._container.add_stream(self.encoder, rate=int(self.cfg.fps))
            self._configure(stream, self.capture.width, self.capture.height, self.encoder)
            self._stream = stream
            self._seg_frames = 0
            self._log.debug("开始写入切片 %s", path.name)
            return True
        except Exception as exc:
            self.last_error = "创建切片容器失败: %s" % exc
            self._log.error(self.last_error)
            self._container = None
            self._stream = None
            return False

    def _close_segment(self) -> None:
        if self._container is None:
            return
        try:
            if self._stream is not None:
                for pkt in self._stream.encode(None):
                    self._container.mux(pkt)
                    self._packets += 1
        except Exception as exc:
            self._log.warning("flush 编码器失败: %s", exc)
        try:
            self._container.close()
        except Exception as exc:
            self._log.warning("关闭切片容器失败: %s", exc)
        self._container = None
        self._stream = None
        path = self.output_dir / ("video_%d.ts" % self._seg_no)
        if path.exists() and path.stat().st_size > 0:
            self._register_segment(path, self._seg_no)
            self._seg_no += 1
        else:
            self._log.warning("切片 video_%d.ts 为空，丢弃", self._seg_no)

    def _register_segment(self, path: Path, num: int) -> None:
        with self._seg_lock:
            if num not in self._latest:
                self._latest.append(num)
        self.segment_count += 1
        try:
            digest = sign_segment(path, self.cfg.sign_key or "dev-sign-key", self.sid)
            path.with_suffix(path.suffix + ".sig").write_text(digest, encoding="utf-8")
        except Exception as exc:
            self._log.warning("切片签名失败: %s", exc)
        self._enforce_disk_cap()
        text, dur = self._verify_segment(path)
        self._last_seg_seconds = dur
        try:
            self._bytes_total += path.stat().st_size
        except Exception:
            pass
        self._log.info("Recorder.%s 切片 %d 完成（%d 字节，累计 %d 片）%s",
                       self.key, num, path.stat().st_size, self.segment_count, text)
        if self._on_segment is not None:
            try:
                self._on_segment(self.key, path, num)
            except Exception:
                pass

    def _verify_segment(self, path: Path):
        """回读切片，确认时长/帧数与预期相符（防 pts 时间基错乱）。"""
        expect = float(max(1, int(self.cfg.segment_seconds)))
        try:
            cont = av.open(str(path))
            stream = cont.streams.video[0]
            frames = 0
            try:
                for packet in cont.demux(stream):
                    if getattr(packet, "size", 0):
                        frames += 1
            except Exception:
                pass
            dur = 0.0
            try:
                dur = float(stream.duration * stream.time_base)
            except Exception:
                pass
            cont.close()
            if dur <= 0 and frames:
                dur = frames / float(max(1, int(self.cfg.fps)))
            flag = "OK" if dur >= expect * 0.5 else "时长异常"
            if dur < expect * 0.5:
                self._log.warning("切片 %s 时长仅 %.2fs（预期 %.1fs），时间基可能不对",
                                  path.name, dur, expect)
            return "｜%.2fs / %d 帧 %s" % (dur, frames, flag), dur
        except Exception as exc:
            return "｜回读失败: %s" % exc, 0.0

    def _enforce_disk_cap(self) -> None:
        """单路录像超过 max_stream_mb 时，删除最老的切片（保留最近 3 片与 live 列表）。"""
        cap_mb = int(getattr(self.cfg, "max_stream_mb", 0) or 0)
        if cap_mb <= 0:
            return
        try:
            files = sorted(self.output_dir.glob("video_*.ts"),
                           key=lambda f: int(f.stem.split("_")[-1]))
            total = sum(f.stat().st_size for f in files)
            keep = set(self.get_latest_segments())
            limit = cap_mb * 1024 * 1024
            for f in files:
                if total <= limit:
                    break
                try:
                    num = int(f.stem.split("_")[-1])
                except Exception:
                    continue
                if num in keep:
                    continue
                size = f.stat().st_size
                f.unlink(missing_ok=True)
                sig = f.with_suffix(f.suffix + ".sig")
                if sig.exists():
                    sig.unlink(missing_ok=True)
                total -= size
            if total > limit:
                self._log.warning("Recorder.%s 已达磁盘上限 %dMB，当前 %.0fMB",
                                  self.key, cap_mb, total / 1048576.0)
        except Exception as exc:
            self._log.debug("磁盘上限检查失败: %s", exc)

    def _open_hls_container(self) -> bool:
        try:
            self._container = av.open(
                str(self.output_dir / "video.m3u8"), mode="w", format="hls",
                options={
                    "hls_time": str(self.cfg.segment_seconds),
                    "hls_list_size": "0",
                    "hls_flags": "append_list+independent_segments",
                    "hls_segment_filename": str(self.output_dir / "video_%d.ts"),
                    "hls_segment_type": "mpegts",
                })
            stream = self._container.add_stream(self.encoder, rate=int(self.cfg.fps))
            self._configure(stream, self.capture.width, self.capture.height, self.encoder)
            self._stream = stream
            return True
        except Exception as exc:
            self.last_error = "创建 HLS 容器失败: %s" % exc
            self._log.error(self.last_error)
            self._container = None
            return False

    def _flush_hls(self) -> None:
        if self._container is None:
            return
        try:
            if self._stream is not None:
                for pkt in self._stream.encode(None):
                    self._container.mux(pkt)
        except Exception:
            pass
        try:
            self._container.close()
        except Exception:
            pass
        self._container = None
        self._stream = None

    # -- 主循环 -----------------------------------------------------
    def _encode(self, img) -> None:
        t0 = time.time()
        if cv2 is not None and (img.shape[1] != self.out_w or img.shape[0] != self.out_h):
            img = cv2.resize(img, (self.out_w, self.out_h), interpolation=cv2.INTER_AREA)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        try:
            frame = frame.reformat(format="yuv420p")
        except Exception:
            pass
        frame.pts = self._frame_index
        frame.time_base = self._tb
        self._frame_index += 1
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
            self._packets += 1
        self._seg_frames += 1
        self._last_progress = time.time()
        self._maybe_keyframe(img)
        self._cost_ms += (time.time() - t0) * 1000.0
        if self._frame_index in (1, 10) or self._frame_index % 100 == 0:
            avg = self._cost_ms / max(1, self._frame_index)
            self._log.info("Recorder.%s 已编码 %d 帧 / mux %d 包 / 切片 %d / 单帧均耗 %.0fms（上限 %.0fms）",
                           self.key, self._frame_index, self._packets, self.segment_count,
                           avg, 1000.0 / max(1, int(self.cfg.fps)))

    def _maybe_keyframe(self, img) -> None:
        """按间隔抽一帧缩略图交给上传通道（比传整片省 90% 带宽）。"""
        if self._on_keyframe is None:
            return
        interval = float(getattr(self.cfg, "keyframe_interval", 0) or 0)
        if interval <= 0:
            return
        now = time.time()
        if now - self._kf_last < interval:
            return
        self._kf_last = now
        try:
            if cv2 is None:
                return
            h, w = img.shape[:2]
            maxw = int(getattr(self.cfg, "keyframe_width", 960) or 960)
            if w > maxw:
                scale = maxw / float(w)
                img = cv2.resize(img, (maxw, int(h * scale)))
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                return
            self._on_keyframe(self.key, buf.tobytes(), int(now))
        except Exception as exc:
            self._log.debug("关键帧抽取失败: %s", exc)

    def _record_loop(self) -> None:
        interval = 1.0 / max(1, int(self.cfg.fps))
        while not self._stop.is_set():
            t0 = time.time()
            try:
                img = self.capture.capture_frame()
                self._encode(img)
                if self.muxer == "segment" and self._seg_frames >= self._seg_target:
                    self._close_segment()
                    if not self._open_next_segment():
                        break
            except Exception as exc:
                self.last_error = str(exc)
                self._log.error("Recorder.%s 编码帧失败: %s", self.key, exc)
                self._stop.set()
                self.recording = False
                break
            delay = interval - (time.time() - t0)
            if delay > 0:
                time.sleep(delay)

    def _watchdog(self) -> None:
        """只做卡死检测：长时间没有任何进展就标记失败。"""
        while not self._stop.is_set():
            time.sleep(1.0)
            stalled = time.time() - self._last_progress
            if stalled > self.cfg.segment_watchdog:
                self.last_error = ("%d 秒无进展（已编码 %d 帧 / mux %d 包 / 切片 %d）"
                                   % (stalled, self._frame_index, self._packets,
                                      self.segment_count))
                self._log.error("Recorder.%s %s", self.key, self.last_error)
                self._stop.set()
                self.recording = False
                break
# ---------------------------------------------------------------- 服务层

class CaptureService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.recorders = {}
        self.screens = []
        self.cameras = []
        self._sid = "unknown"
        self.uploader = None
        self.uploader_session = None
        self._lock = threading.Lock()
        self._log = get_logger(cfg, "capture")

    @property
    def sid(self) -> str:
        return self._sid

    @sid.setter
    def sid(self, value) -> None:
        self._sid = value or "unknown"

    def start_screen(self, idx, name) -> bool:
        key = device_key(name, idx)
        with self._lock:
            rec = self.recorders.get(key)
            if rec and rec.recording:
                return True
            try:
                cap = create_screen_capture(name, int(idx), self.cfg.fps)
            except Exception as exc:
                self._log.error("屏幕采集初始化失败: %s", exc)
                return False
            rec = Recorder(cap, key, self.sid, self.cfg, on_segment=self._on_segment,
                           on_keyframe=self._on_keyframe, kind="screen")
            ok = rec.start()
            self.recorders[key] = rec
            if ok and key not in self.screens:
                self.screens.append(key)
        if ok:
            self._wait_preview(rec)
        return ok

    def start_camera(self, idx, name) -> bool:
        key = device_key(name, idx)
        with self._lock:
            rec = self.recorders.get(key)
            if rec and rec.recording:
                return True
            try:
                cap = create_camera_capture(name, int(idx), self.cfg.fps)
            except Exception as exc:
                self._log.error("摄像头初始化失败: %s", exc)
                return False
            rec = Recorder(cap, key, self.sid, self.cfg, on_segment=self._on_segment,
                           on_keyframe=self._on_keyframe, kind="camera")
            ok = rec.start()
            self.recorders[key] = rec
            if ok and key not in self.cameras:
                self.cameras.append(key)
        if ok:
            self._wait_preview(rec)
        return ok

    def _wait_preview(self, rec) -> None:
        """让 startScreenRecorder 等到第一片真正落盘再返回，避免前端拿到空播放列表。"""
        wait = float(getattr(self.cfg, "preview_wait_seconds", 0) or 0)
        if wait <= 0:
            return
        started = time.time()
        if rec.wait_first_segment(wait):
            self._log.info("预览就绪: %s（等待 %.1fs，已有切片 %s）",
                           rec.key, time.time() - started, rec.get_latest_segments())
        else:
            self._log.warning("预览未就绪: %s（等待 %.1fs 仍无切片，error=%s）",
                              rec.key, time.time() - started, rec.last_error or "-")

    def _on_segment(self, rec_key: str, path, num: int) -> None:
        if (self.uploader is not None and self.uploader_session is not None
                and getattr(self.uploader, "enabled", False)):
            self.uploader.submit(self.uploader_session, rec_key, path, num)

    def health(self) -> dict:
        alive = [k for k, r in self.recorders.items() if r.recording]
        problems = []
        if not self.screens:
            problems.append("未选择屏幕")
        if not self.cameras:
            problems.append("未选择摄像头")
        for k, r in self.recorders.items():
            if not r.recording:
                problems.append("%s 未在录制: %s" % (k, r.last_error or "已停止"))
        ok = bool(self.screens) and bool(self.cameras) and bool(alive)
        detail = {
            "ok": ok,
            "screens": list(self.screens),
            "cameras": list(self.cameras),
            "recorders": {k: {"recording": r.recording, "segments": r.segment_count,
                              "encoder": r.encoder, "error": r.last_error}
                          for k, r in self.recorders.items()},
            "problems": problems,
        }
        self._log.debug("Health check - Screens: %s, Cameras: %s, Recorders: %s",
                        detail["screens"], detail["cameras"], list(detail["recorders"]))
        return detail

    def _on_keyframe(self, rec_key: str, jpeg: bytes, ts: int) -> None:
        if (self.uploader is not None and self.uploader_session is not None
                and getattr(self.uploader, "enabled", False)):
            self.uploader.submit_frame(self.uploader_session, rec_key, jpeg, ts)

    def debug_state(self) -> dict:
        """一条 /debug/state 就能看全：编码器、帧数、包数、切片、失败原因。"""
        out = {}
        for key, r in self.recorders.items():
            out[key] = {
                "recording": r.recording,
                "encoder": r.encoder,
                "muxer": r.muxer,
                "out_size": "%dx%d" % (r.out_w, r.out_h),
                "kind": r.kind,
                "bitrate_kbps": r._bitrate_kbps(),
                "frames": r._frame_index,
                "packets": r._packets,
                "segments": r.segment_count,
                "segments_list": r.get_latest_segments(),
                "last_error": r.last_error,
                "avg_frame_ms": round(r._cost_ms / max(1, r._frame_index), 1),
                "last_segment_seconds": round(r._last_seg_seconds, 2),
                "bytes_total": r._bytes_total,
                "kbps": round(r._bytes_total * 8 / 1000.0
                              / max(1.0, r._frame_index / max(1, int(r.cfg.fps))), 0),
                "dir": str(r.output_dir),
            }
        return out

    def names(self) -> list:
        return list(self.screens) + list(self.cameras)

    def get(self, key: str):
        return self.recorders.get(key)

    def stop_all(self) -> None:
        for rec in list(self.recorders.values()):
            try:
                rec.stop()
            except Exception:
                pass
