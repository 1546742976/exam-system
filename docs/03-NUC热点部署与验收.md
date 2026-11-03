# NUC 自身热点部署与验收

当前确认地址为 **10.42.0.1**。保留现有热点；不假定 /24、DHCP 地址池或网卡名称。考试期间 NUC、教师机和学生机均离线。

## 1. 只读核验与备份

在 NUC 本机执行，记录实际热点连接名、无线接口、SSID、地址/前缀和 DHCP 配置，不输出热点密码：

```bash
nmcli --version
nmcli -f NAME,UUID,TYPE,DEVICE connection show --active
nmcli device status
ip -br address
ip -4 route
ip -6 route
iw list
iw dev
sudo nft list ruleset
ss -lntup
```

设置 `AP_CONNECTION`、`AP_IFACE` 为上述实际值，运行：

```bash
nmcli connection show "$AP_CONNECTION"
iw dev "$AP_IFACE" station dump
```

确认 wireless.mode 为 ap、接口地址含 10.42.0.1，记录实际前缀；检查网卡 AP 模式、频段、驱动和已连接站点。SSID、地址池及租期按现有配置核验，确保覆盖考试人数和时长。任何配置变更前将 NetworkManager 连接文件及防火墙配置备份到仅管理员可读目录，记录原活动连接；备份可能含密码，不随客户端分发。

## 2. 离线与访问控制

NetworkManager 的 `ipv4.method=shared` 会提供 DHCP/DNS 并配置 NAT；它不是离线隔离开关。依据：[NetworkManager 官方配置文档](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html)。

在 NUC 本机控制台断开非考试上联（校园网、有线、第二无线接口、VPN、USB 网络），防止自动重连；不得远程关闭承载当前管理会话的连接。保留 AP 连接，不执行全局 `nmcli networking off`。

检查 IPv4 和 IPv6 路由、防火墙，确保热点流量不能转发至其他接口；有既有防火墙时将规则整合进去，不清空规则集。隔离应覆盖 IPv4/IPv6，允许热点 DHCP/DNS、学生到 NUC TCP 8888、NUC 到学生 TCP 34519 及其响应。HydroOJ 只绑定 127.0.0.1:8889。AP 客户端隔离需实测 NUC 抓图仍可用后再启用。

重启热点及 NUC 后重复核验，防止自动恢复上联或共享转发。DNS 请求失败或单个公网探针失败，不能单独证明断网。

## 3. 学生端与网络体检

学生连接既有 SSID，在客户端填写 `10.42.0.1:8888`。在管理员 PowerShell 中按实际 EXE 位置建立仅允许 NUC 的入站规则：

```powershell
New-NetFirewallRule -DisplayName 'CKYF NUC monitor' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 34519 -RemoteAddress 10.42.0.1 -Profile Any -Program 'C:\实际路径\main.dist\main.exe'
```

将 `EXAM_CIDR` 设置为从 NUC 实际地址/前缀得到的网段，将 `STUDENT_IP` 设置为实际学生地址，然后在 server 目录运行：

```bash
python tools/net_check.py --expect "$EXAM_CIDR" --gw 10.42.0.1 --peers "$STUDENT_IP"
```

学生机执行 `ipconfig /all`、`route print -4`、`route print -6`，核实 DHCP 信息；验证 10.42.0.1:8888 可达、8889 不可达。分别验证 IPv4/IPv6 外网连接失败，断开手机热点及其他网络。NUC 本机检查无法替代学生端检查。

## 4. 最多 110 人、超过 3 小时正式验收

先两台验证登录、考试提交、心跳告警、关键帧上传、教师截图和随机抽查，再按实际考试人数同时连接。记录人数、网卡型号/驱动、信道、信号强度、CPU、内存、磁盘、无线掉线、接口错误率与延迟。

保持现有每 30 秒关键帧、2fps 本地录像基线。模拟同步登录和关键帧上传、教师连续查看、单机短暂断网恢复、网关重启后重新登录。确认录像不因网络故障中断，恢复后新关键帧可上传，失败计数可见。完整录像不自动补传，考后按实际需求收集。

持续至少一个完整考试时长。目标：所有学生获取地址并成功登录/提交；无未恢复掉线；正常网络下心跳不超过配置静默阈值；关键帧和教师截图可用；无持续积压、内存增长或磁盘耗尽。性能数据须留档，不能用软件并发测试替代无线容量。

目前未接入 NUC，以上实机项均为待验收。

## 5. 恢复与构建

考试结束先保存学生录像、服务端数据库和上传目录，再按记录恢复原上联；仅撤销本次新增的防火墙规则，不覆盖整个防火墙。若热点配置曾修改，使用备份恢复对应连接并在本机控制台重新激活。

客户端构建使用 Python 3.11 x64：创建 `.venv-build`，安装 `requirements-build.lock.txt`，运行 `python build.py nuitka`。构建脚本先验证虚拟环境能否运行，不再仅检查文件存在，也不自动升级依赖。分发整个 `dist/main.dist`。PyInstaller 为可选工具，需另行安装，当前锁文件以 Nuitka 构建为准。


## 6. 本次考试容量基线（2026-11-04 更新）

实际需求：最多约 110 人，考试超过 3 小时。暂按 4 小时连续录制/联网验收；正式时长若更长，压力测试延长到实际考试时长加 1 小时。4 小时不是考试时长上限。

- DHCP 至少能同时容纳 110 台学生机及教师/备用设备，建议核验至少 120 个可分配地址；这不是要求修改现有子网，先确认当前前缀、地址池及网卡最大关联数。
- 按每人一屏一路摄像头、300+100 kbps 估算：每小时约 180 MB，每人 4 小时约 720 MB，110 人合计约 79.2 GB；这是分散在学生机的本地录像，不是当前关键帧上传带宽。容器开销、码率波动和其他屏幕需额外计入，建议每台至少预留 3 GB 可用空间，并实测画面清晰度。
- 当前每路 1200 MiB 达到上限后会删除旧切片。300 kbps 屏幕路理论约 9.3 小时触顶，但这不是保证；还须计入上次考试残留录像。开考前先归档既有录像，确认本次目录容量，不能仅凭“超过 3 小时”判断不会滚动删除。
- 每路每 30 秒一张关键帧，两路共 220 张/30秒，即平均约 7.33 张/秒。若每张实测 100 KB，则平均约 5.87 Mbps，4 小时服务器存档约 10.56 GB；实际 JPEG 大小和同步峰值必须测量。服务器建议至少留出 30 GB 专用余量（不含 OJ、数据库及完整录像归档）。
- 110 人同时登录、同时上传、教师截图需在实机测峰值；按 30、60、90、110 台逐级扩展，再做满员长时间测试。软件并发通过不代表单无线网卡可接入 110 台。
- 网关登录 Cookie 12 小时、考试 Cookie 6 小时、管理员 Cookie 8 小时；4 小时基线可覆盖。计时从登录/进入考试开始，包括候场。插件另有 5 小时令牌；正式流程若接近这些期限，应先明确总时长再调整或实现续期。教师截图令牌按请求新签发，不需要把短期教师令牌延长为整个考试时长。
