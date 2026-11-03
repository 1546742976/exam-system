"""环境检测：虚拟机 / 显存 / 内存 / 联网 / 局域网串通 / 热点。

每个监视器都是可调用对象，返回 alert 列表：
    [{"id": "vm-dmi-xxx", "text": "...", "meta": {...}}]
id 首段决定前端分类：vm- / vram- / mem- / net- / lan- / hotspot-
"""
from __future__ import annotations

import ipaddress
import os
import platform
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common import Config, get_logger

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


def _alert(prefix: str, key: str, text: str, **meta) -> dict:
    item = {"id": "%s-%s" % (prefix, key), "text": text}
    if meta:
        item["meta"] = meta
    return item


class BaseMonitor:
    name = "base"

    def __call__(self) -> list:
        raise NotImplementedError


class VMMonitor(BaseMonitor):
    """WMI 厂商/型号 + 网卡 MAC OUI 判虚拟机。"""
    name = "vm"
    KEYWORDS = ["vmware", "virtualbox", "vbox", "qemu", "kvm", "hyper-v", "virtual machine",
                "bochs", "xen", "parallels", "innotek", "bhyve", "aliyun", "tencent cloud"]
    VIRTUAL_OUI = ("00:05:69", "00:0c:29", "00:1c:14", "00:50:56",  # vmware
                   "08:00:27", "0a:00:27",                          # virtualbox
                   "52:54:00", "00:15:5d", "00:16:3e", "00:03:ff")

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.vm")

    def __call__(self) -> list:
        out = []
        if platform.system().lower() != "windows":
            return out
        try:
            import wmi
            c = wmi.WMI()
            for sysobj in c.Win32_ComputerSystem():
                text = ((sysobj.Manufacturer or "") + " " + (sysobj.Model or "")).lower()
                for kw in self.KEYWORDS:
                    if kw in text:
                        out.append(_alert("vm", "dmi", "检测到虚拟机环境：%s" % kw,
                                          manufacturer=sysobj.Manufacturer, model=sysobj.Model))
                        break
            for nic in c.Win32_NetworkAdapter():
                mac = (getattr(nic, "MACAddress", "") or "").replace("-", ":").lower()
                if len(mac) < 8:
                    continue
                if any(mac.startswith(oui) for oui in self.VIRTUAL_OUI):
                    out.append(_alert("vm", "mac-" + mac[:8].replace(":", ""),
                                      "检测到虚拟网卡：%s" % mac, mac=mac))
        except Exception as exc:
            self.log.debug("WMI 检测失败: %s", exc)
        return out


class MemMonitor(BaseMonitor):
    """单进程内存阈值 + 敏感进程名（远程控制/通讯/录屏软件）。"""
    name = "mem"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.mem")

    def __call__(self) -> list:
        out = []
        if psutil is None:
            return out
        limit_mib = int(self.cfg.mem_limit_mib)
        names = [n.lower() for n in (self.cfg.sensitive_processes or [])]
        try:
            total = psutil.virtual_memory().total / 1048576.0
        except Exception:
            total = 0.0
        try:
            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    info = proc.info
                    pname = (info.get("name") or "")
                    rss = getattr(info.get("memory_info"), "rss", 0) / 1048576.0
                    low = pname.lower()
                    for kw in names:
                        if kw and kw in low:
                            out.append(_alert("mem", "sensitive-" + low.replace(" ", ""),
                                              "检测到敏感进程：%s (pid %d)" % (pname, info["pid"]),
                                              pid=info["pid"], process=pname))
                            break
                    if rss >= limit_mib:
                        out.append(_alert("mem", "process-6gb-%d" % info["pid"],
                                          "%s 占用内存 %.0f MiB（≥%d MiB）" % (pname, rss, limit_mib),
                                          pid=info["pid"], process=pname, rss_mib=round(rss)))
                    elif total > 0 and rss / total >= self.cfg.mem_percent:
                        out.append(_alert("mem", "process-highpct-%d" % info["pid"],
                                          "%s 占用 %.0f MiB（≥总内存 %d%%）"
                                          % (pname, rss, int(self.cfg.mem_percent * 100)),
                                          pid=info["pid"], process=pname, rss_mib=round(rss)))
                except Exception:
                    continue
        except Exception as exc:
            self.log.debug("内存检测失败: %s", exc)
        return out


class VRAMMonitor(BaseMonitor):
    """nvidia-smi / rocm-smi 显存占用。"""
    name = "vram"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.vram")

    def __call__(self) -> list:
        out = []
        out.extend(self._nvidia())
        out.extend(self._rocm())
        return out

    def _nvidia(self) -> list:
        out = []
        try:
            raw = subprocess.check_output(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "ignore")
        except Exception:
            return out
        try:
            total = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "ignore")
            total_mib = int(re.sub(r"[^0-9]", "", total.split()[0]) or 0)
        except Exception:
            total_mib = 0
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
                mib = int(re.sub(r"[^0-9]", "", parts[1]) or 0)
            except Exception:
                continue
            if mib >= self.cfg.vram_limit_mib:
                out.append(_alert("vram", "nvidia-%d" % pid,
                                  "进程 %d 占用显存 %d MiB（≥%d）" % (pid, mib, self.cfg.vram_limit_mib),
                                  pid=pid, vram_mib=mib))
            elif total_mib and mib / total_mib >= self.cfg.vram_percent:
                out.append(_alert("vram", "nvidia-highpct-%d" % pid,
                                  "进程 %d 占用显存 %d MiB（≥显卡总量 %d%%）"
                                  % (pid, mib, int(self.cfg.vram_percent * 100)),
                                  pid=pid, vram_mib=mib, total_mib=total_mib))
        return out

    def _rocm(self) -> list:
        out = []
        try:
            raw = subprocess.check_output(["rocm-smi", "--showpid"],
                                          stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "ignore")
        except Exception:
            return out
        for line in raw.splitlines():
            m = re.search(r"(\d+)\s+(\S+)\s+(\d+)\s*(MiB|MB)?", line, re.I)
            if not m:
                continue
            try:
                pid = int(m.group(1))
                mib = int(m.group(3))
                if (m.group(4) or "").upper() in ("MB", "MIB") and mib >= self.cfg.vram_limit_mib:
                    out.append(_alert("vram", "rocm-%d" % pid,
                                      "进程 %d 占用显存约 %d MiB（rocm）" % (pid, mib),
                                      pid=pid, vram_mib=mib))
            except Exception:
                continue
        return out


class NetMonitor(BaseMonitor):
    """探活外网。局域网内 OJ 可达不触发，只有真能上外网才报警。"""
    name = "net"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.net")

    def __call__(self) -> list:
        out = []
        if requests is None:
            return out
        for url in self.cfg.net_probe_urls:
            try:
                resp = requests.get(url, timeout=self.cfg.net_probe_timeout,
                                    headers={"User-Agent": "exam-client/2.0"})
                if resp.status_code < 500:
                    out.append(_alert("net", "reachable", "检测到互联网联通：" + url, url=url))
                    break
            except Exception as exc:
                self.log.debug("网络探测失败 %s: %s", url, exc)
        return out


class LANMonitor(BaseMonitor):
    """局域网串通探测：扫同网段常见共享/远控端口。

    正常学生机只应开放本机 34519，出现 3389/5900/445/8000 等即报警。
    """
    name = "lan"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.lan")
        self._cache = []
        self._cache_at = 0.0
        self._lock = threading.Lock()

    def _local_subnet(self):
        if psutil is None:
            return None
        try:
            for _, addrs in psutil.net_if_addrs().items():
                for a in addrs:
                    if a.family == socket.AF_INET and not a.address.startswith("127.") \
                            and not a.address.startswith("169.254."):
                        mask = a.netmask or "255.255.255.0"
                        return ipaddress.ip_network("%s/%s" % (a.address, mask), strict=False)
        except Exception:
            pass
        return None

    def _probe(self, host: str, port: int, timeout: float) -> bool:
        sock = socket.socket()
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((host, port)) == 0
        except Exception:
            return False
        finally:
            sock.close()

    def __call__(self) -> list:
        now = time.time()
        with self._lock:
            if now - self._cache_at < self.cfg.lan_scan_interval:
                return list(self._cache)
        net = self._local_subnet()
        if net is None or net.num_addresses > 1024:
            return []
        my_ips = set()
        try:
            for addrs in psutil.net_if_addrs().values():
                for a in addrs:
                    my_ips.add(a.address)
        except Exception:
            pass
        hosts = [str(h) for h in net.hosts() if str(h) not in my_ips]
        found = []
        try:
            with ThreadPoolExecutor(max_workers=self.cfg.lan_scan_concurrency) as pool:
                futures = {pool.submit(self._probe, h, p, self.cfg.lan_scan_timeout): (h, p)
                           for h in hosts for p in self.cfg.lan_scan_ports}
                for fut, hp in futures.items():
                    try:
                        if fut.result():
                            found.append(hp)
                    except Exception:
                        continue
        except Exception as exc:
            self.log.debug("局域网扫描失败: %s", exc)
        alerts = []
        for host, port in found:
            alerts.append(_alert("lan", "share-%s-%d" % (host.replace(".", "_"), port),
                                 "局域网主机 %s 开放端口 %d（可能存在远控/共享）" % (host, port),
                                 host=host, port=port))
        self.log.debug("局域网扫描完成，命中 %d 个", len(alerts))
        with self._lock:
            self._cache = alerts
            self._cache_at = time.time()
        return list(alerts)


class HotspotMonitor(BaseMonitor):
    """Windows 移动热点 / 共享网段检测。"""
    name = "hotspot"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.hotspot")

    def __call__(self) -> list:
        out = []
        if psutil is None:
            return out
        try:
            for nic, addrs in psutil.net_if_addrs().items():
                for a in addrs:
                    if a.family == socket.AF_INET and a.address.startswith("192.168.137."):
                        out.append(_alert("hotspot", "ics",
                                          "检测到移动热点网段：%s (%s)" % (nic, a.address),
                                          interface=nic, address=a.address))
        except Exception:
            pass
        return out


class AIMonitor(BaseMonitor):
    """本地 AI 检测（考试断网场景：只看本机是否在跑大模型/生成式 AI）。

    三条线索，任一命中即报警：
      1. 进程名命中常见本地 AI 客户端 / 推理引擎；
      2. 本机监听常见 AI 服务端口（Ollama 11434、LM Studio 1234…）；
      3. 磁盘上存在模型文件（.gguf/.safetensors…），可关。
    允许参考本地代码：编辑器、IDE、浏览器一律不算。
    """
    name = "ai"

    PROC_KEYWORDS = [
        # 本地推理引擎 / 服务
        "ollama", "llama-server", "llama-cli", "llamafile", "koboldcpp", "kobold",
        "text-generation-webui", "oobabooga", "localai", "vllm", "tabbyapi",
        "gpt4all", "llamacpp", "llama_cpp", "mistralrs", "exllama",
        # 桌面客户端
        "lm studio", "lmstudio", "jan.exe", "anythingllm", "msty", "chatbox",
        "cherry studio", "cherrystudio", "nextchat", "chatwise", "open-webui",
        "cursor", "copilot", "continue.dev", "codeium", "tabnine",
        # 本地生成类
        "comfyui", "stable-diffusion", "sd-webui", "automatic1111", "invokeai",
        "fooocus", "kohya", "whisper", "faster-whisper", "cosyvoice", "chattts",
        "gpt-sovits", "so-vits-svc", "rvc",
    ]
    AI_PORTS = [11434, 1234, 1337, 5000, 5001, 7860, 8000, 8080, 8188, 3000, 8501]
    MODEL_EXTS = (".gguf", ".safetensors", ".ckpt", ".onnx", ".pt", ".pth", ".bin")

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(cfg, "monitor.ai")
        self._model_cache = []
        self._model_at = 0.0

    def __call__(self) -> list:
        out = []
        out.extend(self._check_processes())
        out.extend(self._check_ports())
        out.extend(self._check_models())
        return out

    def _check_processes(self) -> list:
        out = []
        if psutil is None:
            return out
        kws = [k.lower() for k in (getattr(self.cfg, "ai_process_keywords", None)
                                   or self.PROC_KEYWORDS)]
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                exe = (info.get("exe") or "").lower()
                cmd = " ".join(info.get("cmdline") or []).lower()
                hay = name + " " + exe + " " + cmd
                for kw in kws:
                    if kw and kw in hay:
                        out.append(_alert("ai", "process-" + kw.replace(" ", "_"),
                                          "检测到本地 AI 程序：%s (pid %d)"
                                          % (info.get("name"), info["pid"]),
                                          pid=info["pid"], process=info.get("name")))
                        break
            except Exception:
                continue
        return out

    def _check_ports(self) -> list:
        out = []
        ports = getattr(self.cfg, "ai_ports", None) or self.AI_PORTS
        for port in ports:
            sock = socket.socket()
            sock.settimeout(0.15)
            try:
                if sock.connect_ex(("127.0.0.1", int(port))) == 0:
                    out.append(_alert("ai", "port-%d" % int(port),
                                      "本机 %d 端口有 AI 服务在监听（可能是本地模型推理）"
                                      % int(port), port=int(port)))
            except Exception:
                pass
            finally:
                sock.close()
        return out

    def _check_models(self) -> list:
        if not getattr(self.cfg, "ai_scan_models", True):
            return []
        now = time.time()
        ttl = float(getattr(self.cfg, "ai_model_scan_interval", 600) or 600)
        if now - self._model_at < ttl:
            return list(self._model_cache)
        dirs = getattr(self.cfg, "ai_model_dirs", None) or []
        found = []
        limit = int(getattr(self.cfg, "ai_model_find_limit", 3) or 3)
        for raw in dirs:
            try:
                base = os.path.expandvars(os.path.expanduser(raw))
                if not base or not os.path.isdir(base):
                    continue
                for root, _subdirs, files in os.walk(base):
                    depth = root[len(base):].count(os.sep)
                    if depth > 2:
                        continue
                    for fn in files:
                        if fn.lower().endswith(self.MODEL_EXTS):
                            p = os.path.join(root, fn)
                            size = 0
                            try:
                                size = os.path.getsize(p)
                            except Exception:
                                pass
                            if size >= 100 * 1024 * 1024:      # 只报 >100MB 的模型
                                found.append(_alert(
                                    "ai", "model-" + hashlib_short(p),
                                    "发现本地大模型文件：%s（%.1f GB）"
                                    % (fn, size / 1073741824.0),
                                    path=p, size=size))
                    if len(found) >= limit:
                        break
            except Exception as exc:
                self.log.debug("模型目录扫描失败 %s: %s", raw, exc)
            if len(found) >= limit:
                break
        self._model_cache = found
        self._model_at = now
        if found:
            self.log.warning("发现 %d 个本地模型文件", len(found))
        return list(found)


def hashlib_short(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:8]


MONITORS = {
    "vm": VMMonitor,
    "vram": VRAMMonitor,
    "mem": MemMonitor,
    "net": NetMonitor,
    "lan": LANMonitor,
    "hotspot": HotspotMonitor,
    "ai": AIMonitor,
}


class MonitorService:
    """按固定间隔轮询所有监视器，按 id 去重合并后回调上报。"""

    def __init__(self, cfg: Config, callback=None):
        self.cfg = cfg
        self.callback = callback
        self.interval = max(1.0, float(cfg.monitor_interval))
        self.log = get_logger(cfg, "monitor")
        self.monitors = []
        for key in cfg.enabled_monitors:
            cls = MONITORS.get(key)
            if cls is None:
                continue
            try:
                self.monitors.append(cls(cfg))
            except Exception as exc:
                self.log.warning("监视器 %s 初始化失败: %s", key, exc)
        self.latest = []
        self.last_report_at = 0.0
        self.round = 0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def sample(self) -> list:
        merged, seen = [], set()
        for mon in self.monitors:
            try:
                for item in mon() or []:
                    key = str(item.get("id") or item.get("text"))
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(item)
            except Exception as exc:
                self.log.exception("监视器 %s 采样异常: %s", getattr(mon, "name", "?"), exc)
        with self._lock:
            self.latest = merged
            self.round += 1
        if self.callback is not None:
            try:
                self.callback(merged)
                self.last_report_at = time.time()
            except Exception as exc:
                self.log.exception("上报回调失败: %s", exc)
        return merged

    def get_latest(self) -> list:
        with self._lock:
            return list(self.latest)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="monitor-service", daemon=True)
        self._thread.start()
        self.log.info("MonitorService 启动，间隔 %.1fs，启用 %s",
                      self.interval, [m.name for m in self.monitors])

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception:
                self.log.exception("MonitorService 循环异常")
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self.log.info("MonitorService 已停止")
