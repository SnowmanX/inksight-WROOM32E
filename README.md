# InkSight × Waveshare 4.2" e-Paper Cloud Module

本仓库 fork 自 [datascale-ai/inksight](https://github.com/datascale-ai/inksight)，新增对**微雪 4.2inch e-Paper Cloud Module**（固件不可下载场景）的服务端下发支持。

> 上游 InkSight 仓库的原始说明已保留在 [`README.upstream.md`](./README.upstream.md) / [`README_ZH.upstream.md`](./README_ZH.upstream.md)。
> 协议与架构详解：[`backend/WAVESHARE_README.md`](./backend/WAVESHARE_README.md) · 端到端时序：[`backend/ARCHITECTURE.md`](./backend/ARCHITECTURE.md)

---

## 这个项目能做什么

把微雪原版"必须用自家 App 配网、必须连微雪云"的 4.2" 墨水屏，改成"用你自己电脑上的网站控制"：

- ✅ 选一个模式（如 `DAILY` / `POETRY` / `WEATHER`），网页上**实时预览**会成什么样
- ✅ 一键推送到墨水屏，**不刷固件、不改硬件**
- ✅ 内置 **30 个 InkSight 模式**（每日、诗词、天气、AI 简报等）
- ✅ 可选：**先全白清屏**（消除残影）· **推完即睡**（关 WiFi 省电）· **OTA 升级固件**（高危）

---

## 与上游 InkSight 的区别

| 维度 | 原版 InkSight | 本 fork |
|---|---|---|
| 目标设备 | ESP32-C3 + 4.2" e-paper（自刷固件） | **微雪 4.2" e-Paper Cloud Module**（固件不可改） |
| 设备端职责 | 解析 JSON + 渲染 + 刷屏 | 只接收 **预渲染好的 1bpp 位图** + 触发刷屏 |
| 渲染位置 | ESP32 固件 | **服务端**（InkSight pipeline 在电脑上跑） |
| 通信协议 | 设备拉 JSON (HTTP) | **Waveshare 私有 TCP 6868 协议** |

---

## 硬件与网络准备

### 你需要准备的东西

| 项目 | 说明 |
|---|---|
| 微雪 4.2" e-Paper Cloud Module | 1 台，固件为出厂版本（不需要改） |
| 电脑（Windows / macOS / Linux） | 跑后端 + bridge，IP 在路由器里固定 |
| Wi-Fi 路由器 | 电脑和设备在**同一个局域网** |
| 微雪配网 App | iOS / Android，用于第一次把设备配上 Wi-Fi |

> 💡 **不知道设备密码？** 出厂默认是 `123456`，本项目全程使用这个默认值。

---

## 每次开机怎么启动

### 一键启动（推荐）

仓库根目录有现成脚本，**改一个 IP 就能用**：

1. 用文本编辑器打开 `start.bat`（Windows）或 `start.sh`（macOS / Linux）
2. 把 `DEVICE_HOST=192.168.1.195` 改成**你电脑的局域网 IP**（不确定就 `ipconfig` / `ifconfig` 查）
3. 运行：
   - **Windows**：双击 `start.bat`
   - **macOS / Linux**：`bash start.sh`
4. 会自动弹出 3 个终端窗口（后端 / Bridge / Webapp），等 10-20 秒加载完
5. 浏览器打开 `http://127.0.0.1:3000/cloud-module`
6. 关闭时直接关掉那 3 个窗口，或运行 `bash stop.sh`

### 手动启动（3 个独立终端）

**首次安装**才需要装依赖，之后每次启动只需要跑下面 3 个命令（每个开一个终端）：

**终端 1 — InkSight 后端**（端口 8080）

```bash
cd backend
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uvicorn api.index:app --host 0.0.0.0 --port 8080
```

**终端 2 — Waveshare Bridge**（HTTP 9000 + TCP 6868）

```bash
cd backend
source .venv/bin/activate
python -m backend.scripts.waveshare_bridge --device-ip 192.168.1.195 --port 9000
```

> `192.168.1.195` = 你电脑的局域网 IP（**不是设备 IP！**）

**终端 3 — Webapp**（端口 3000）

```bash
cd webapp
npm run dev
```

---

## 安装步骤

### 0. 克隆仓库

```bash
git clone https://github.com/SnowmanX/inksight-waveshare-cloud-module.git
cd inksight-waveshare-cloud-module
```

### 1. 启动 InkSight 后端（端口 8080）

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env       # 可选：填 LLM key 以启用 AI 模式

uvicorn api.index:app --host 0.0.0.0 --port 8080 --reload
```

启动后访问 `http://127.0.0.1:8080/docs` 能看到 FastAPI 文档就 OK 了。

> ⚠️ **不填 LLM key 也能跑**：30 个模式大部分会走 fallback（用占位文字），不是 30 个一模一样。填上 `OPENAI_API_KEY` 或 `DASHSCOPE_API_KEY` 后会走真 pipeline，每个模式内容都不一样。

### 2. 启动 Waveshare Bridge（HTTP 9000 + TCP 6868）

**新开一个终端**，仍在 `backend/` 目录、激活 venv：

```bash
# Windows PowerShell
$env:BRIDGE_LOG_FILE = "D:\Hardware\esp32\cloudModule\inksight-waveshare-cloud-module\backend\bridge.log"
python -m backend.scripts.waveshare_bridge --device-ip 192.168.1.195 --port 9000

# macOS / Linux
BRIDGE_LOG_FILE=./bridge.log python -m backend.scripts.waveshare_bridge --device-ip 192.168.1.195 --port 9000
```

参数说明：

| 参数 | 含义 | 示例 |
|---|---|---|
| `--device-ip` | 电脑在局域网内的 IP（**不是设备 IP！**） | `192.168.1.195` |
| `--port` | Bridge HTTP 控制端口 | `9000` |

启动成功你会看到：

```
[waveshare_bridge] INFO: bridge ready, device=192.168.1.195:6868
[passive v29] listening on 0.0.0.0:6868
Uvicorn running on http://0.0.0.0:9000
```

**Bridge 同时开两个端口**：

- `0.0.0.0:6868` — TCP 被动监听，**等设备主动连进来**后推图
- `0.0.0.0:9000` — HTTP 控制端，**供 webapp 调用**（推图 / 预览 / 状态）

> 📌 `6868` 是微雪私有协议端口，**不是 InkSight 后端**（那是 8080）。两个必须同时跑。

### 3. 启动 Webapp（端口 3000）

**再开一个终端**，回到仓库根目录：

```bash
cd webapp
cp .env.example .env.local      # 默认配置即可

npm install                     # 首次需要，会装 ~2-3 分钟
npm run dev
```

启动成功访问 `http://127.0.0.1:3000/cloud-module` 进入"微雪墨水屏"控制台。

`.env.local` 默认值（一般不用改）：

```ini
WAVESHARE_BRIDGE_BASE=http://127.0.0.1:9000   # 上面 Bridge 的地址
INKSIGHT_BACKEND_API_BASE=http://127.0.0.1:8080 # InkSight 后端
```

### 4. 让微雪设备连到你的电脑

1. 用微雪配网 App 把设备连上你家的 Wi-Fi
2. App 里把"**目标主机**"设成你电脑的局域网 IP（如 `192.168.1.195`）
3. "**目标主机端口**"设成 `6868`
4. 保存

设备每隔 ~24 秒会主动连 `192.168.1.195:6868` 一次。Bridge 日志里看到 `device connected: 192.168.1.46:xxxxx` 就说明握手成功。

---

## 跑通后能用 Web 做什么

打开 `http://127.0.0.1:3000/cloud-module`：

| 按钮 / 复选框 | 作用 |
|---|---|
| **模式下拉框** | 选 30 个内置模式之一（DAILY / POETRY / WEATHER ...） |
| **预览** | 在网页上看到墨水屏将要显示的样子（PNG） |
| **推到设备** | 渲染 → 1bpp → 推到设备。设备下次连入时（约 24 秒内）自动刷屏 |
| **一键全推** | 把 30 个模式**依次**推到设备，每张约 6 秒（适合一次性看完所有模式） |
| ☑ **先全屏清白** | 推图前先发一帧全白数据，**消除残影**（多花 ~5s） |
| ☑ **推完即睡** | 推图后向设备发 `;S/` 让它关 WiFi 省电（屏幕内容保持不变） |
| **Arm OTA (Dangerous)** | 刷固件。**会改设备固件，慎用！** |

---

## 直接用 HTTP API 调 Bridge

不需要 webapp，`curl` 也能玩：

```bash
# 查设备状态
curl http://127.0.0.1:9000/status

# 预览一个模式（返回 PNG）
curl -o daily.png http://127.0.0.1:9000/preview/DAILY

# 推送一个模式到设备
curl -X POST http://127.0.0.1:9000/push \
  -H "Content-Type: application/json" \
  -d '{"persona": "DAILY", "sleep_after": true, "clear_before": true}'

# 一次推 30 个模式
curl -X POST http://127.0.0.1:9000/push_all

# 列出所有 30 个模式
curl http://127.0.0.1:9000/modes
```

`POST /push` 完整 payload：

```json
{
  "persona": "DAILY",       // 必填, 模式名 (DAILY / POETRY / WEATHER ...)
  "sleep_after": false,     // 可选, 推图后让设备进入 ;S/ 关机
  "clear_before": false     // 可选, 推图前先发一帧全白清屏 (消残影)
}
```

---

## 项目结构

```
inksight-waveshare-cloud-module/
├── backend/                          # Python 后端 + InkSight pipeline
│   ├── api/                          # FastAPI 主入口 (端口 8080)
│   ├── core/                         # 模式 registry、pipeline、LLM
│   ├── scripts/
│   │   ├── waveshare_protocol.py     # 指令/数据/收尾帧构造 + checksum
│   │   ├── waveshare_passive_server.py  # 6868 被动监听 + 推图 + OTA
│   │   └── waveshare_bridge.py       # 主入口：渲染 + 1bpp + 推图 + HTTP API
│   ├── WAVESHARE_README.md           # 协议 & 改造点详解
│   ├── ARCHITECTURE.md               # 端到端时序图
│   └── requirements.txt
├── webapp/                           # Next.js 16 前端
│   └── app/
│       ├── cloud-module/             # 微雪墨水屏控制台页面
│       └── api/cloud-module/[...path]/  # 代理 webapp → bridge
├── README.upstream.md                # 上游 InkSight 原始 README (英文)
├── README_ZH.upstream.md             # 上游 InkSight 原始 README (中文)
└── README.md                         # 你正在读的这份
```

---

## 常见问题

### 1. 启动时提示 `[WinError 10061] 由于目标计算机积极拒绝，无法连接`
**正常**。Bridge 在启动时 ping 一次设备，没连上也无所谓——设备会自己每隔 24 秒来连。

### 2. 推了图但屏幕没反应
看 Bridge 日志有没有 `device connected: 192.168.1.46:xxxxx`。如果完全没有：
- 检查设备"目标主机 / 端口"是否设对（电脑 IP + 6868）
- 检查电脑和设备在**同一个 Wi-Fi**
- 检查电脑防火墙是否放行 6868 端口（**Windows 防火墙最常拦截**）

### 3. 屏幕显示但内容乱码
99% 是 1bpp 位图 bit 序错了。请看 [`backend/ARCHITECTURE.md`](./backend/ARCHITECTURE.md) 的"调试路径"一节。

### 4. 推图后屏幕没关机（设备还在反复刷）
确保在 webapp 上**勾选了"推完即睡"**复选框。详见 `backend/WAVESHARE_README.md`。

### 5. 屏幕上"先全屏清白"按钮点了，左上角还是有残影
可能设备固件版本对"清屏后重新进数据模式"的支持有差异。看 Bridge 日志中 `F/[after-clear]` 响应是否正常（应该是 `2446230000`）。

### 6. 我想自己写模式
InkSight 的模式是 JSON 描述文件，放在 `backend/core/modes/builtin/*.json`。参考现有的 `daily.json` 写一个新的，**重启 backend** 就会被自动加载。

---

## 致谢

- 原版 InkSight：[datascale-ai/inksight](https://github.com/datascale-ai/inksight)
- 协议参考：[headblockhead/wavesharecloud](https://github.com/headblockhead/wavesharecloud/blob/master/DISPLAYDOCS.md)
- 官方 SDK：[Waveshare Cloud Module Wiki](https://www.waveshare.com/wiki/4.2inch_e-Paper_Cloud_Module)
- 官方 demo 路径：`Cloud_WIN/lib/tcp_server/tcp_sver.py`

## 社区

- Discord: <https://discord.gg/5Ne6D4YNf>
- BiliBili: <https://www.bilibili.com/video/BV1nSNcziE7q/>
