# 考试系统（client + server + addon）

```
exam-system/
├─ client/     学生端（Python + pywebview，复用 static/ 前端）
├─ server/     考试网关（反代 HydroOJ + 告警/上传 + 教师控制台）
│  ├─ tools/   校验脚本（allowlist / net_check / port_scan / verify_sig / smoke_test）
│  └─ data/    exam.db、uploads/、allowlist.json
├─ addon/      HydroOJ 插件（`ckyf-exam-addon`，安装时目录名必须是 exam）
└─ docs/       使用测试文档、配置手册
```

## 快速开始

### 服务端（NUC / Linux）

```bash
cd server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入随机生成的管理员口令、会话密钥和 sign_key
python gateway.py                     # 对外 http://<NUC>:8888
```

HydroOJ 内部地址必须是 `127.0.0.1:8889`，不要暴露到局域网。

### 学生端（Windows）

```bat
cd client
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
python main.py                        :: 接口地址填 <NUC>:8888
```

### 教师端

```text
浏览器打开 http://<NUC>:8888/exam/admin
```

完整部署、测试和开考流程见：
`docs/01-使用测试文档.md`。

## 与 HydroOJ addon 的关系

- addon 负责**考试门禁**（`handler/after/*` 钩子，靠 UA/`_ncec` 判定）与教师面板；
- server 网关负责**告警落库、上传缺口、指纹白名单、反向代理**，并在转发时把 UA 补成 `exam-client/2.0`，避免被 addon 拦成 403；
- 两端共用 **addon 的 RSA 密钥对**：私钥只保存在 `server/keys/private.pem`，由插件配置 `privateKeyFile` 读取；公钥在 `client/keys/public.pem`。

## 文档

- `docs/02-完整使用手册.md` —— 完整操作手册（账号口令、服务管理、比赛配置、监考、测试、故障排查）
- `docs/01-使用测试文档.md` —— 部署与测试流程
- `docs/00-配置手册.md` —— 参数和校验脚本速查
