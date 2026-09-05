# InkSight × Waveshare 4.2" e-Paper Cloud Module

本仓库 fork 自 [datascale-ai/inksight](https://github.com/datascale-ai/inksight)，新增对**微雪 4.2inch e-Paper Cloud Module**（无固件下载场景）的服务端下发支持。

## 🎯 与上游的区别

| 维度 | 原版 InkSight | 本 fork |
|---|---|---|
| 目标设备 | ESP32-C3 + 4.2" e-paper | **微雪 4.2" e-Paper Cloud Module**（固件不可改） |
| 设备端职责 | 解析 JSON + 渲染 + 刷屏 | 仅接收 **预渲染好的 1bpp 位图** + 触发刷屏 |
| 渲染位置 | ESP32 固件 | **服务端**（InkSight pipeline 在后端跑完） |
| 协议 | JSON over HTTP | **Waveshare 私有 TCP 6868 协议** |

**核心改造点**：把 InkSight 的"模式 → JSON → 设备渲染"管线，改为"模式 → 后端渲染成 PNG → 抖动成 1bpp → Waveshare TCP 6868 推图"。

## 🏗️ 架构

```
┌────────────────┐  1. 配置模式    ┌──────────────────────┐
│  浏览器 Webapp │ ──────────────▶ │  FastAPI Backend     │
│  (webapp/)     │ ◀──── 预览 ── │  (现有 InkSight)      │
└────────────────┘                 │  30+ 模式 / pipeline │
                                   └──────────┬───────────┘
                                              │ HTTP / 共享内存
                                              ▼
                                   ┌──────────────────────┐
                                   │  Waveshare Bridge    │  ← 新增
                                   │  (backend/scripts/   │
                                   │   waveshare_bridge.py)│
                                   │  • 调 pipeline 渲染  │
                                   │  • PNG → 1bpp 抖动   │
                                   │  • 协议封装          │
                                   │  • TCP 6868 推送     │
                                   └──────────┬───────────┘
                                              │ TCP 6868
                                              ▼
                                   ┌──────────────────────┐
                                   │  微雪 4.2" Cloud     │
                                   │  Module (固件不动)    │
                                   └──────────────────────┘
```

## 🆕 新增文件（backend/scripts/）

| 文件 | 作用 | 行数 |
|---|---|---|
| `waveshare_protocol.py` | 协议封装：指令模式 + 数据模式 + checksum | 200+ |
| `waveshare_passive_server.py` | 6868 被动监听：等设备连入并推图 | 170+ |
| `waveshare_bridge.py` | **主入口**：整合 backend 渲染 + 协议推送 + HTTP 控制 | 350+ |
| `fake_waveshare_device.py` | 假设备模拟（开发调试用） | 100+ |
| `find_waveshare_device.py` | 局域网扫 IP 工具 | 90+ |
| `offline_selftest.py` | 离线单元测试（不依赖设备） | 60+ |
| `probe_waveshare_password.py` | 设备密码探测 | 40+ |
| `test_active_mode.py` | 主动模式连通性测试 | 30+ |
| `waveshare_bridge.env.example` | 环境变量模板 | 15 |

## 🚀 快速开始

### 1. 启动 InkSight 后端

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # 可选：填 LLM key 启用 AI 模式
# 启动 (不需要 uvicorn, 模式渲染可用脚本直接调)
```

### 2. 启动 Waveshare Bridge

```bash
cd backend
$env:WAVESHARE_DEVICE_IP="192.168.1.46"   # 你的微雪设备 IP
$env:WAVESHARE_DEVICE_PASSWORD="123456"   # 默认密码
$env:BRIDGE_PORT="9000"                    # HTTP 控制端口
$env:BRIDGE_LOG_FILE="./bridge.log"
python -m backend.scripts.waveshare_bridge
```

启动后：
- `0.0.0.0:6868` 被动监听（等设备 TCP 连入）
- `0.0.0.0:9000` HTTP 控制端（push / preview / status）

### 3. 设备配置

通过微雪 App 或蓝牙配网：
- 目标主机 = 电脑 IP（如 `192.168.1.195`）
- 目标端口 = `6868`
- Wi-Fi SSID/密码 = 你的路由器

### 4. 触发推送

```bash
# HTTP 推送
curl -X POST http://127.0.0.1:9000/push -H "Content-Type: application/json" -d '{"persona":"POETRY"}'

# 浏览器预览
open http://127.0.0.1:9000/preview/POETRY
```

## 📡 协议关键点（踩过的坑）

完整协议逆向见 [`backend/scripts/waveshare_protocol.py`](backend/scripts/waveshare_protocol.py) 的 docstring。要点：

1. **设备是 TCP 客户端**：默认每 30~60 秒主动连服务端 6868
2. **握手流程**：`;G/` (Get ID) → `;C/` (查锁) → `;N<pw>/` (解锁) → `;F/` (进数据模式) → 推图 → `;D/` (刷屏)
3. **数据帧**：`0x57 + 4B addr + 4B len + 1B num + data + 1B cs`（**len = 实际数据长度，不是固定 1024**）
4. **最后一帧**：15000 字节 = 14×1024 + 664，所以最后一帧 addr=14336, len=664
5. **Checksum**：XOR 除 `0x57` 头之外的所有字节（含 addr/len/num/data）
6. **收尾帧**：`0x57 + 0x00×11 + 0x00`（13 字节，cs=0x00）
7. **1bpp 位图**：PIL `Image.tobytes()` 输出 = MSB-first, 0=黑, 1=白，**和官方 getbuffer() 完全一致，无需转换**
8. **`;D/` 后必须 sleep 5s+ 等墨水屏全刷**（黑/白切换周期）
9. **`;F/` 后必须 sleep 0.1s**（官方 Cloud_WIN 源码有这一行）

## 🧪 离线测试

```bash
python -m backend.scripts.offline_selftest
```

不依赖真设备，验证：
- 协议帧构造（指令/数据/收尾）
- 15 帧分片正确性
- Bridge 全链路 fake device 往返

## 📜 致谢

- 原版 InkSight：[datascale-ai/inksight](https://github.com/datascale-ai/inksight)
- 协议参考：[headblockhead/wavesharecloud](https://github.com/headblockhead/wavesharecloud/blob/master/DISPLAYDOCS.md)
- 官方 SDK：[Waveshare Cloud Module Wiki](https://www.waveshare.com/wiki/4.2inch_e-Paper_Cloud_Module)
- 官方 demo 路径：`Cloud_WIN/lib/tcp_server/tcp_sver.py`
