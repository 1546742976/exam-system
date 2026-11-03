"""公共模块：配置、日志、JWT 校验、路径。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import logging.handlers
import platform
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_SENSITIVE = [
    # 远程控制 / 屏幕共享
    "todesk", "sunlogin", "向日葵", "anydesk", "teamviewer", "rustdesk",
    "parsec", "vnc", "mstsc", "radmin", "gotohttp", "airdroid",
    # 即时通讯（常用于传答案 / 共享屏幕）
    "qq", "wechat", "weixin", "dingtalk", "wemeet", "feishu", "lark",
    # 录屏 / 推流
    "obs64", "obs32", "bandicam", "fraps", "evcapture",
]


@dataclass
class Config:
    # 本地服务
    host: str = "0.0.0.0"
    port: int = 34519
    media_dir: str = "media"
    log_dir: str = "logs"
    static_dir: str = "static"
    # 录制
    fps: int = 2                       # 3 小时考试：2fps 足够回放取证
    segment_seconds: int = 3
    segment_watchdog: int = 20
    bitrate_kbps: int = 0             # 全局兜底码率（0=不限制，优先用下面分路设置）
    screen_bitrate_kbps: int = 300    # 屏幕路码率上限（kb/s）：保证文字可读
    camera_bitrate_kbps: int = 100    # 摄像头路码率上限（kb/s）：够看清人即可
    capture_scale: float = 0.5        # 采集缩放比例（0.5 = 分辨率减半）
    max_stream_mb: int = 0            # 单路录像磁盘上限(MB)，超出滚动删除最老的切片；0=不限
    muxer: str = "segment"            # segment=自己切段（推荐）；hls=FFmpeg 的 hls muxer
    encoders: list = field(default_factory=lambda: [
        "h264_nvenc", "h264_qsv", "h264_amf", "libx264"])
    # 采集
    screenshot_interval: float = 2.0
    preview_wait_seconds: float = 5.0   # 开始录制后最多等多久才回前端（等第一片）
    # 监控
    monitor_interval: float = 5.0
    # 断网考场只需盯「本地 AI」；其它检测项保留但默认关闭
    # 断网考场：本地 AI + 虚拟机 + 外网（流动监考需要实时盯人）
    enabled_monitors: list = field(default_factory=lambda: ["ai", "vm", "net"])
    # 本地 AI 检测参数
    ai_process_keywords: list = field(default_factory=list)   # 空=用内置名单
    ai_ports: list = field(default_factory=lambda: [
        11434, 1234, 1337, 5000, 5001, 7860, 8000, 8080, 8188, 3000, 8501])
    ai_scan_models: bool = True
    ai_model_scan_interval: float = 600.0
    ai_model_find_limit: int = 3
    ai_model_dirs: list = field(default_factory=lambda: [
        "~/.ollama/models",
        "~/AppData/Local/Programs/Ollama",
        "~/.cache/lm-studio/models",
        "~/AppData/Roaming/Jan/data",
        "~/AppData/Local/nomic.ai/GPT4All",
        "~/AppData/Local/LM-Studio",
        "D:/models", "C:/models",
    ])
    mem_limit_mib: int = 6144
    mem_percent: float = 0.70
    vram_limit_mib: int = 6144
    vram_percent: float = 0.70
    sensitive_processes: list = field(default_factory=lambda: list(DEFAULT_SENSITIVE))
    net_probe_urls: list = field(default_factory=lambda: ["https://www.baidu.com"])
    net_probe_timeout: float = 5.0
    lan_scan_ports: list = field(default_factory=lambda: [3389, 5900, 5938, 8000, 8080, 445, 139])
    lan_scan_timeout: float = 0.25
    lan_scan_interval: float = 60.0
    lan_scan_concurrency: int = 64
    # 鉴权（本地 34519 的教师令牌校验）
    jwt_alg: str = "RS256"              # RS256 或 HS256
    jwt_public_key_file: str = "keys/public.pem"
    jwt_secret: str = ""                # HS256 时使用
    jwt_audience: str = "ckyf-exam"     # 为空则不校验 aud
    teacher_token_ttl: int = 7200
    # 切片签名（HMAC-SHA256，服务端持有同一密钥）
    sign_key: str = ""
    # 边录边传（证据存档到服务器）
    upload_enabled: bool = True
    upload_mode: str = "keyframes"    # keyframes=只传关键帧(省带宽) / segments=传完整切片
    keyframe_interval: float = 15.0   # 关键帧上传间隔（秒）
    keyframe_width: int = 960         # 关键帧缩略图宽度
    # 其它
    inject_url_redirect: bool = True    # 是否强制把窗口拉回考试页
    exam_path: str = "/exam/"


def load_config() -> Config:
    cfg = Config()
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except Exception as exc:  # 配置坏了也要能启动
            print("config.json 解析失败，使用默认配置:", exc)
    for env_key, attr in (
        ("CKYF_PORT", "port"),
        ("CKYF_HOST", "host"),
        ("CKYF_JWT_SECRET", "jwt_secret"),
        ("CKYF_SIGN_KEY", "sign_key"),
    ):
        if os.environ.get(env_key):
            val = os.environ[env_key]
            setattr(cfg, attr, int(val) if attr == "port" else val)
    return cfg


def media_root(cfg: Config) -> Path:
    p = Path(cfg.media_dir)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir(cfg: Config) -> Path:
    p = Path(cfg.log_dir)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- 依赖自检

# (导入名, pip 名, 分组, 缺失后果)
DEPENDENCIES = [
    ("fastapi",      "fastapi",            "必需", "本地服务无法启动"),
    ("uvicorn",      "uvicorn",            "必需", "本地服务无法启动"),
    ("requests",     "requests",           "必需", "登录/上报不可用"),
    ("psutil",       "psutil",             "必需", "内存/局域网检测失效"),
    ("mss",          "mss",                "必需", "截图与录屏不可用"),
    ("PIL",          "Pillow",             "必需", "截图与抽查帧不可用"),
    ("numpy",        "numpy",              "必需", "录屏不可用"),
    ("av",           "av",                 "必需", "录屏不可用（HLS 编码）"),
    ("cv2",          "opencv-python",      "可选", "摄像头不可用"),
    ("webview",      "pywebview",          "可选", "无窗口，只能用浏览器打开"),
    ("jwt",          "PyJWT",              "可选", "教师令牌校验失效"),
    ("wmi",          "WMI",                "可选", "虚拟机检测失效"),
    ("pygrabber",    "pygrabber",          "可选", "摄像头名称枚举退化"),
]

CORE_MODULES = {"fastapi", "uvicorn", "requests", "mss", "PIL", "numpy", "av"}
PIP_NAMES = {m: p for m, p, level, why in DEPENDENCIES}


def check_dependencies() -> list:
    """返回 [(导入名, pip名, 分组, 影响)]，只列缺失的。"""
    import importlib.util
    missing = []
    for module, pip_name, level, why in DEPENDENCIES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append((module, pip_name, level, why))
    return missing


WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
SEP = chr(92)


def _win_build() -> int:
    """Windows 内部版本号，如 19045；取不到返回 0。"""
    try:
        ver = platform.version().split(".")
        return int(ver[2]) if len(ver) >= 3 else 0
    except Exception:
        return 0


def has_webview2() -> bool:
    """检测 WebView2 Evergreen Runtime 是否安装（注册表三处 + 文件系统兜底）。"""
    if platform.system().lower() != "windows":
        return False
    try:
        import winreg
        paths = [
            (winreg.HKEY_LOCAL_MACHINE,
             SEP.join(["SOFTWARE", "WOW6432Node", "Microsoft", "EdgeUpdate",
                       "Clients", WEBVIEW2_GUID])),
            (winreg.HKEY_LOCAL_MACHINE,
             SEP.join(["SOFTWARE", "Microsoft", "EdgeUpdate", "Clients", WEBVIEW2_GUID])),
            (winreg.HKEY_CURRENT_USER,
             SEP.join(["Software", "Microsoft", "EdgeUpdate", "Clients", WEBVIEW2_GUID])),
        ]
        for root, path in paths:
            try:
                with winreg.OpenKey(root, path) as key:
                    pv, _ = winreg.QueryValueEx(key, "pv")
                    if pv and pv != "0.0.0.0":
                        return True
            except OSError:
                continue
    except Exception:
        pass
    # 文件系统兜底
    for base in (os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        exe = Path(base) / "Microsoft" / "EdgeWebView" / "Application"
        if exe.is_dir() and any(exe.iterdir()):
            return True
    return False


def check_host_requirements() -> list:
    """Windows 运行环境自检。返回 [(级别, 说明)]，级别 error 表示无法运行。"""
    problems = []
    system = platform.system().lower()
    if system != "windows":
        problems.append(("error", "本项目仅支持 Windows 10/11，当前系统：%s" % platform.system()))
        return problems

    build = _win_build()
    if build and build < 17763:
        problems.append(("error",
                         "Windows 版本过低（内部版本 %d），需要 Win10 1809(17763) 及以上" % build))
    elif build < 19041:
        problems.append(("warn", "建议 Win10 20H1(19041)+，当前 %d" % build))

    if not has_webview2():
        problems.append(("error",
                         "未检测到 WebView2 运行时：窗口会白屏。"
                         "请安装 Microsoft Edge WebView2 Evergreen Runtime (x64)"))

    if platform.machine().lower() not in ("amd64", "x86_64"):
        problems.append(("warn", "当前架构 %s，仅 x64 经过验证" % platform.machine()))
    return problems


def missing_core(missing: list) -> list:
    return [m for m in missing if m[0] in CORE_MODULES]


_loggers = {}


def get_logger(cfg: Config, name: str = "client") -> logging.Logger:
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(
        log_dir(cfg) / (name + ".log"), maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    _loggers[name] = logger
    return logger


# ---------------------------------------------------------------- JWT 校验

def _b64url_decode(s: str) -> bytes:
    s = s.encode("ascii")
    s += b"=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def verify_hs256(token: str, secret: str, audience: str = "") -> dict:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("token 格式错误")
    signing_input = (head_b64 + "." + body_b64).encode("ascii")
    expect = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, _b64url_decode(sig_b64)):
        raise ValueError("签名不匹配")
    payload = json.loads(_b64url_decode(body_b64))
    _check_claims(payload, audience)
    return payload


def verify_rs256(token: str, public_key_pem: str, audience: str = "") -> dict:
    try:
        import jwt as pyjwt  # PyJWT
    except ImportError:
        raise ValueError("缺少 PyJWT，请 pip install pyjwt cryptography")
    payload = pyjwt.decode(
        token, public_key_pem, algorithms=["RS256"],
        options={"verify_aud": bool(audience)},
        audience=audience or None)
    _check_claims(payload, audience, already_exp=True)
    return payload


def _check_claims(payload: dict, audience: str = "", already_exp: bool = False) -> None:
    now = int(time.time())
    if not already_exp:
        exp = payload.get("exp")
        if exp is not None and int(exp) < now:
            raise ValueError("token 已过期")
        nbf = payload.get("nbf")
        if nbf is not None and int(nbf) > now + 60:
            raise ValueError("token 尚未生效")
    if audience and payload.get("aud") not in (None, audience):
        raise ValueError("aud 不匹配")


class TeacherAuth:
    """校验服务端下发的教师令牌。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.public_key = ""
        path = Path(cfg.jwt_public_key_file)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists():
            self.public_key = path.read_text(encoding="utf-8")
        if cfg.jwt_alg.upper() == "RS256" and not self.public_key:
            raise RuntimeError("缺少公钥文件: " + str(path))

    def verify(self, token: str) -> dict:
        alg = self.cfg.jwt_alg.upper()
        if alg == "HS256":
            if not self.cfg.jwt_secret:
                raise ValueError("未配置 jwt_secret")
            return verify_hs256(token, self.cfg.jwt_secret, self.cfg.jwt_audience)
        return verify_rs256(token, self.public_key, self.cfg.jwt_audience)


def sign_segment(path: Path, sign_key: str, sid: str) -> str:
    """切片签名：HMAC-SHA256(sign_key, 内容 + sid)，比原版固定盐 SHA1 可靠。"""
    h = hmac.new(sign_key.encode("utf-8"), digestmod=hashlib.sha256)
    h.update(sid.encode("utf-8"))
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_config(cfg: Config) -> str:
    d = asdict(cfg)
    for k in ("jwt_secret", "sign_key"):
        if d.get(k):
            d[k] = "***"
    return json.dumps(d, ensure_ascii=False, indent=2)
