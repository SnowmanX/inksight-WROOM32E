# 端到端架构：Waveshare 4.2" Cloud Module × InkSight

本文档描述从**用户在浏览器选模式**到**墨水屏像素刷新**的完整链路，以及本 fork 的改动落在哪一层。

## 1. 角色

| 角色 | 实体 | 职责 |
|---|---|---|
| 用户 | 浏览器（webapp/） | 选择模式 / 预览 / 触发推送 |
| 后端 | InkSight FastAPI (端口 8000) | 加载 mode registry、运行 30+ 模式的 `ContentFn` + `RenderFn`、输出 400×300 PNG |
| 桥接器 | `backend/scripts/waveshare_bridge.py` (HTTP 9000 / TCP 6868) | 调后端渲染 → PNG→1bpp 抖动 → 走微雪协议推图 |
| 设备 | Waveshare 4.2" e-Paper Cloud Module | 固件不可改；每 30~60s 作为 TCP 客户端连 6868，接收预渲染位图，刷屏 |

## 2. 端到端时序

```
┌──────┐   1.选模式     ┌────────────┐  2.调pipeline   ┌──────────────┐
│ webapp│ ───────────▶ │ FastAPI    │ ──────────────▶ │ mode registry │
│       │ ◀─ 3.预览PNG ─┤ (InkSight) │ ◀─ 4.PIL.Image ─┤ + renderers  │
└──┬───┘               └──────┬─────┘                 └──────────────┘
   │                           │
   │ 5. POST /push {persona}   │ 6. /api/render
   ▼                           ▼
┌────────────────────┐    ┌──────────────────────────┐
│ waveshare_bridge   │───▶│ png_bytes_to_1bpp()      │
│  (HTTP 9000)       │    │  • PIL.LANCZOS 缩到 400×300
│                    │    │  • native_dither.atkinson_bw
│                    │    │  • Image.tobytes()        │
└────────┬───────────┘    └────────────┬─────────────┘
         │                             │ 15000 bytes (MSB-first)
         │ 7. push_image()             ▼
         ▼
┌──────────────────────────────────────────────────────┐
│ TCP 6868（被动服务）                                  │
│  socket.accept → push_image(sock, bw)                │
│    ;G/ → ;C/ → ;N<pw>/ → ;F/ → sleep 0.1s           │
│    15 帧数据（addr 0..14336, len 1024/664）            │
│    closeFrame（13 字节 0x57+11×0x00+0x00）            │
│    ;D/ → sleep 5s（等墨水屏全刷）                     │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
              Waveshare 4.2" 屏幕（黑/白）
```

## 3. 关键代码路径

| 步骤 | 入口 | 实现 |
|---|---|---|
| 模式注册 | `backend/core/mode_registry.py` | 内置 Python 模式 + `backend/core/modes/builtin/*.json` + `custom/*.json` |
| 内容生成 | `backend/core/pipeline.py:generate_and_render` | `ContentFn(ctx) → dict`，再喂给 JSON 模式或 Python renderer |
| 渲染输出 | `generate_and_render` | 返回 `PIL.Image`，已 resize 到 400×300 |
| PNG→1bpp | `backend/scripts/waveshare_bridge.py:png_bytes_to_1bpp` | LANCZOS 缩放 → Atkinson 抖动（`backend/core/native_dither.py`）→ `tobytes()` |
| 协议封装 | `backend/scripts/waveshare_protocol.py` | `build_cmd / build_data / build_close_frame`，含 `WaveshareDevice` dataclass |
| 推图主流程 | `backend/scripts/waveshare_passive_server.py:push_image` | 完整 7 步（握手 + 15 帧 + 收尾 + ;D/） |
| 桥接 HTTP 入口 | `backend/scripts/waveshare_bridge.py:make_app` | `/status` `/push` `/preview/{persona}` `/device/ping` |

## 4. 与上游 InkSight 的边界

- **沿用**：所有 mode registry、`generate_and_render` 链路、JSON mode schema、LLM provider、static tables。
- **替换**：设备端固件（不存在了）→ 用 `waveshare_passive_server` + `waveshare_protocol` 替代。
- **新增**：`png_bytes_to_1bpp`（在 bridge 侧做，不污染后端）。

## 5. 失败降级

`render_persona_to_png` 用 `try/except` 包住整个 InkSight pipeline：

1. **首选**：`generate_and_render(persona, ...)` → 真正的 30+ 模式之一。
2. **降级**：任意异常（缺依赖、LLM 不可用、模式不存在）→ `_fallback_render(persona)`，永远能返回一帧合法 PNG。

这样**协议层永远不空**——即使 InkSight 完全炸了，设备也能看到一帧"InkSight + Waveshare 4.2"文字图，证明链路通。

## 6. 调试路径

1. `python -m backend.scripts.offline_selftest` — 协议帧/分片/伪设备往返（不需要真设备）。
2. `python -m backend.scripts.waveshare_bridge` — 启桥接器，看 warm-up 日志（是否装上 alimt stub、是否缓存 1bpp）。
3. `curl -X POST http://127.0.0.1:9000/push -d '{"persona":"DAILY"}'` — 主动推一帧。
4. 设备端按按钮触发连接 → 看 `waveshare_bridge.log` 里 `[passive] device connected: ...` 和 15 帧 ACK。

## 7. 已知坑（写在代码里）

- alimt SDK 缺失：用 `sys.modules` 注入 stub（`_stub_alimt_module`），否则 JSON 模式 `import` 阶段就崩。
- 1bpp 位序：**PIL `tobytes()` = 官方 `getbuffer()`，MSB-first，0=白，1=黑**——不要反转。
- 数据帧长度：最后一帧 `len=664` 不是 `1024`；`addr` 必须按 `1024` 递增。
- 收尾帧：13 字节，`0x57 + 11×0x00 + 0x00`，不能用 `build_data(addr=0, data=b"", num=0)` 代替（已踩过）。
- `;D/` 后必须 `time.sleep(5)` 等墨水屏物理全刷，否则下次连接设备还在刷屏中。
