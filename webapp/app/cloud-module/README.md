# Waveshare 4.2" e-Paper Cloud Module 控制台

`/cloud-module` 页面专门给 **Waveshare 4.2" e-Paper Cloud Module**（固件不可改、走 TCP 6868 推图的那种）使用。
**不依赖** 设备下载固件 —— 模式内容由 InkSight 后端渲染，通过本地桥接器（`backend/scripts/waveshare_bridge.py`）走微雪协议推到设备。

## 架构

```
┌──────────┐   /api/cloud-module/*    ┌─────────────────┐
│  webapp  │ ───────────────────────▶ │ waveshare_bridge │
│  3000    │                          │      9000       │
└──────────┘                          └────────┬────────┘
                                                │ 1) /api/render → PNG
                                                │ 2) PNG→1bpp (Atkinson)
                                                │ 3) TCP 6868 → 设备
                                                ▼
                                   Waveshare 4.2" 屏幕
```

| 层 | 端点 | 用途 |
|---|---|---|
| 浏览器 | `/cloud-module` | 选模式 / 预览 / 推送 |
| Webapp API proxy | `/api/cloud-module/[...path]` | catch-all 转发到 bridge 9000 |
| Bridge | `GET /modes` | 30 个内置模式清单 |
| Bridge | `GET /preview/{persona}` | 渲染 400×300 PNG（不下发设备） |
| Bridge | `POST /push` body `{persona}` | 缓存 1bpp 位图，等下次设备连接时下发 |
| Bridge | `POST /push_all` body `{delay, personas?}` | 按顺序缓存全部模式 |
| Bridge | `GET /status` | 设备地址 / 屏幕尺寸 / 最新缓存 |
| Bridge (TCP) | `0.0.0.0:6868` | 设备主动连进来时推图（按 v27 协议） |

## 启动

需要 **3 个进程**，建议开 3 个终端：

```bash
# 1) InkSight FastAPI（渲染 30 个模式）
cd backend
python -m uvicorn main:app --port 8000

# 2) Waveshare bridge（HTTP 9000 控制面 + TCP 6868 设备面）
cd backend
python -m backend.scripts.waveshare_bridge --host 0.0.0.0 --port 9000

# 3) Next.js webapp（含 /cloud-module 页面）
cd webapp
cp .env.example .env.local        # 确认 WAVESHARE_BRIDGE_BASE=http://127.0.0.1:9000
npm install                       # 首次
npm run dev                       # 默认 3000
```

打开 <http://localhost:3000/cloud-module> 即可。

## 设备端准备（一次性）

在 **Waveshare App** 里给设备配置：

| 字段 | 值 |
|---|---|
| 目标主机 | `192.168.1.195`（电脑 IP，bridge 监听 6868） |
| 目标主机端口 | `6868` |
| 设备密码 | `123456`（与 bridge 默认密码一致） |

## 推图流程

1. **缓存一帧**（不依赖设备）
   - 点页面上的"预览" → 浏览器拿 400×300 PNG
   - 点"推到设备" → bridge 渲染 + 1bpp 抖动 + 缓存到内存
2. **设备拉帧**（设备决定时机）
   - 设备按按钮 / 到定时 / 重连 → 作为 TCP 客户端连 `192.168.1.195:6868`
   - bridge 收到连接 → 按 v27 协议握手 → 推缓存的 1bpp → `;D/` 触发墨水屏物理刷屏
3. **等待墨水屏全刷**（约 5 秒，期间设备无响应）

**注意**：bridge 不主动连设备；设备作为 TCP client 找过来。InkSight 的 `apply-preview`（走 `/api/device/{mac}/apply-preview`）不适用这种固件不可改的云模组。

## 协议层关键点

完整协议细节见 `backend/WAVESHARE_README.md`，端到端流程见 `backend/ARCHITECTURE.md`。简版：

- 握手：`;G/` → `;C/` → `;N123456/` → `;F/`
- 数据：15 帧（14×1024 + 1×664 字节），每帧 `0x57 + 4B addr + 4B len + 1B num + data + 1B cs`
- 收尾：13 字节 `0x57 + 11×0x00 + 0x00`
- 刷屏：`;D/`（之后 sleep 5s 等物理刷完）

## 调试

```bash
# 离线验证 30 个模式（不连设备）
cd backend
python -m backend.scripts.mode_smoke_test
# → logs/mode_smoke.json, 30/30 走真实 InkSight pipeline

# 看 bridge 状态
curl http://127.0.0.1:9000/status

# 手动推一帧
python -c "import urllib.request, json; print(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:9000/push', data=json.dumps({'persona':'DAILY'}).encode(), headers={'Content-Type':'application/json'})).read().decode())"

# 看实时日志
tail -f backend/waveshare_bridge.log
```

## 失败降级

- InkSight pipeline 任意异常 → 桥接器用 `_fallback_render` 返回固定图（白底黑字 + 标题 + 时间 + "Bridge alive"），保证协议层不空
- alimt / openai SDK 缺失 → `waveshare_bridge.py` 在 import 时用 `sys.modules` 注入 stub，让 JSON 模式能直接走 pipeline
- 设备长时间没连 → 缓存的 bw 一直在；下次设备来时直接推

## 与原 webapp 的关系

**完全独立**，不修改任何现有 API：

- 原 `/api/modes`、`/api/preview`、`/api/device/*` —— 走 InkSight 后端，给可下载固件的设备用，未动
- 新增 `/api/cloud-module/[...path]` —— 走 bridge 9000，给不可改固件的云模组用
- 新增 `/cloud-module` 页面 —— 独立 UI，模式清单直接来自 bridge（30 个 InkSight 内置 JSON 模式）
