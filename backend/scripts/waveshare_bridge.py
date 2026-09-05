"""
Waveshare 4.2" e-Paper Cloud Module ↔ InkSight 桥接器（第一版，可跑）。

工作流：
  浏览器(webapp) ──┐
                  ├─► InkSight FastAPI backend (端口 8000)
                  │      └─► generate_and_render() → 400x300 PNG
                  │
  本脚本(桥接) ────┘
        │
        │ 1) 调后端 /api/render 拿到最新 PNG
        │ 2) 1bpp 二值化（Atkinson 抖动）
        │ 3) 转为 400x300 / 15000 字节的位图数据
        │ 4) TCP 6868 按微雪协议推到设备
        │
        └─► Waveshare 4.2" e-Paper Cloud Module

启动方式（开发）：
    python -m backend.scripts.waveshare_bridge --device-ip 192.168.4.1 --port 9000

可被 webapp 通过以下 HTTP 端点控制：
    GET  /status         → 桥接器与设备状态
    POST /push           → {"persona": "POETRY"} 推送指定模式
    GET  /device/ping    → 主动 ping 设备
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional

# 让脚本能以模块或顶层方式运行
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(THIS_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
REPO_ROOT = os.path.dirname(BACKEND_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from PIL import Image

from backend.scripts.waveshare_protocol import (
    WAVESHARE_42_FRAME_BYTES,
    WAVESHARE_42_SCREEN_H,
    WAVESHARE_42_SCREEN_W,
    WaveshareDevice,
)
from backend.scripts.waveshare_passive_server import start_passive_server  # 新增

logger = logging.getLogger("waveshare_bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.environ.get("BRIDGE_LOG_FILE", r"D:\Hardware\esp32\cloudModule\inksight-waveshare-cloud-module\logs\bridge.log"),
            encoding="utf-8",
        ),
    ],
)


# ==================== 图像处理：PNG → 1bpp 400x300 ====================


def _stub_aliyun_modules() -> None:
    """猴子补丁: 注入阿里云 SDK 的 stub, 让 InkSight pipeline 在没装 SDK 时也能跑.

    InkSight 顶层 import 的阿里云包:
      alibabacloud_alimt20181012        (翻译)
      alibabacloud_alimt20181012.client
      alibabacloud_alimt20181012.models
      alibabacloud_tea_openapi          (OpenAPI 通用 SDK, alimt 间接依赖)
      alibabacloud_tea_openapi.models
      alibabacloud_tea_openapi.client

    没装的话 json_content.py 加载就崩, 整个 pipeline fallback 到固定文字图,
    30 个模式之间完全没区别. 我们用 sys.modules 注入 stub, 翻译/SDK 调用都返回原值.

    stub 设计:
      - Request 构造器: 收任意参数
      - Client: 实例化不报错, translate_general/任何方法都返回 body.data = None
        (InkSight 会自己处理 None → 走非翻译分支, 即"原值显示").
    """
    import sys
    import types

    if "alibabacloud_tea_openapi" in sys.modules and "alibabacloud_alimt20181012" in sys.modules:
        return  # 全部已装

    def _install_pkg(name: str) -> types.ModuleType:
        if name in sys.modules:
            return sys.modules[name]
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
        return mod

    def _install_response() -> type:
        class _StubResponse:
            def __init__(self, *a, **kw):
                self.body = type("body", (), {"data": None})()
                self.headers = {}
                self.status_code = 200

        return _StubResponse

    # === alibabacloud_tea_openapi ===
    tea = _install_pkg("alibabacloud_tea_openapi")
    tea_models = _install_pkg("alibabacloud_tea_openapi.models")

    class _TeaModels:
        class TeaRequest:
            def __init__(self, *a, **kw):
                pass

        class TeaModel:
            def __init__(self, *a, **kw):
                pass

    for n, v in _TeaModels.__dict__.items():
        if not n.startswith("_"):
            setattr(tea_models, n, v)

    tea_client = _install_pkg("alibabacloud_tea_openapi.client")

    class _TeaClient:
        def __init__(self, *a, **kw):
            self._cfg = kw

        def call_api(self, *a, **kw):
            return _install_response()()

        def do_rpc_request(self, *a, **kw):
            return _install_response()()

    tea_client.Client = _TeaClient
    setattr(tea, "Client", _TeaClient)
    setattr(tea, "Tea", _TeaClient)
    setattr(tea, "TeaCore", _TeaClient)

    # === alibabacloud_alimt20181012 ===
    alimt = _install_pkg("alibabacloud_alimt20181012")
    alimt_models = _install_pkg("alibabacloud_alimt20181012.models")

    class _StubRequest:
        def __init__(self, *a, **kw):
            self._data = a or kw

    alimt_models.TranslateGeneralRequest = _StubRequest
    setattr(alimt, "models", alimt_models)

    alimt_client = _install_pkg("alibabacloud_alimt20181012.client")

    class _AlimtClient:
        def __init__(self, *a, **kw):
            pass

        def translate_general(self, request, *a, **kw):
            return _install_response()()

        def translate(self, request, *a, **kw):
            return _install_response()()

    alimt_client.Client = _AlimtClient
    setattr(alimt, "Client", _AlimtClient)
    setattr(alimt, "client", alimt_client)

    # === 常见派生包（如果有 json_content 直接 import 这些，也兜住） ===
    for n in (
        "alibabacloud_tea_util",
        "alibabacloud_tea_util.client",
        "alibabacloud_tea_openapi.util",
        "alibabacloud_tea_openapi.endpoint",
    ):
        if n not in sys.modules:
            m = types.ModuleType(n)
            sys.modules[n] = m

    # === openai SDK（InkSight LLM provider 走 openai 兼容协议） ===
    if "openai" not in sys.modules:
        openai_pkg = types.ModuleType("openai")
        openai_pkg.__path__ = []
        sys.modules["openai"] = openai_pkg

        class _StubChoice:
            def __init__(self, *a, **kw):
                self.message = type("msg", (), {"content": "", "role": "assistant"})()

        class _StubCompletion:
            def __init__(self, *a, **kw):
                self.choices = [_StubChoice()]
                self.usage = type("usage", (), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})()

        class _StubChatCompletions:
            def create(self, *a, **kw):
                return _StubCompletion()

        class _StubChat:
            def __init__(self, *a, **kw):
                self.completions = _StubChatCompletions()

        class _StubOpenAI:
            def __init__(self, *a, **kw):
                self.chat = _StubChat()

        class _StubAsyncOpenAI:
            def __init__(self, *a, **kw):
                self.chat = _StubChat()

        class _StubAPIError(Exception):
            pass

        class _StubOpenAIError(_StubAPIError):
            pass

        openai_pkg.OpenAI = _StubOpenAI
        openai_pkg.AsyncOpenAI = _StubAsyncOpenAI
        openai_pkg.APIError = _StubAPIError
        openai_pkg.OpenAIError = _StubOpenAIError
        openai_pkg.ChatCompletion = type("ChatCompletion", (), {"create": staticmethod(lambda *a, **kw: _StubCompletion())})

    logger.info("[waveshare_bridge] installed alimt+tea_openapi+openai stubs (real SDKs not available)")


# 在模块 import 时立即执行 stub, 必须在 backend import 之前
_stub_aliyun_modules()


def png_bytes_to_1bpp(png_bytes: bytes, *, dither: bool = True) -> bytes:
    """v32 终极修正: 撤销 _reverse_bits_in_byte (它一直是错的).

    真相 (实测):
      PIL '1' 模式 tobytes() 输出:
        - bit 7 = 左像素, bit 0 = 右像素 (MSB-first)
        - 0=黑, 1=白
      官方 getbuffer() 输出:
        - buf[i] = 0x80 >> (x%8)  即 bit 7 = 左像素, bit 0 = 右像素 (MSB-first)
        - 1=白, 0=黑 (黑像素 &= ~(mask))
      两者完全一致! 不需要任何转换!

      历史:
        v25-v31 我都加了 _reverse_bits_in_byte → 设备 ACK 但乱码
        v32 撤销 → 应该正常显示

      验证: Image.new('1', 8x1, 1) 全部 1=白
            putpixel((0,0), 0) → tobytes()[0] = 0b01111111
            即 bit 7=0(黑), bit 0=1(白) — 官方 getbuffer 一致
    """
    src = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    if src.size != (WAVESHARE_42_SCREEN_W, WAVESHARE_42_SCREEN_H):
        src = src.resize((WAVESHARE_42_SCREEN_W, WAVESHARE_42_SCREEN_H), Image.LANCZOS)

    gray = src.convert("L")

    try:
        from backend.core import native_dither  # type: ignore

        bw = native_dither.atkinson_bw(gray)
        bw = bw.convert("1", dither=Image.Dither.NONE)
    except Exception as e:  # noqa: BLE001
        logger.warning("native Atkinson 不可用，回退到 Floyd-Steinberg: %s", e)
        bw = gray.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.Dither.NONE)

    if bw.size != (WAVESHARE_42_SCREEN_W, WAVESHARE_42_SCREEN_H):
        bw = bw.resize((WAVESHARE_42_SCREEN_W, WAVESHARE_42_SCREEN_H), Image.NEAREST)

    # v32: 直接 tobytes(), 不反转.
    raw = bw.tobytes()
    if len(raw) != WAVESHARE_42_FRAME_BYTES:
        expected = WAVESHARE_42_FRAME_BYTES
        if len(raw) < expected:
            raw = raw + b"\xFF" * (expected - len(raw))
        else:
            raw = raw[:expected]
    return raw


# ==================== 后端渲染：直接调 InkSight pipeline ====================

async def render_persona_to_png(
    persona: str,
    *,
    mac: str = "test-mac",
    screen_w: int = WAVESHARE_42_SCREEN_W,
    screen_h: int = WAVESHARE_42_SCREEN_H,
    colors: int = 2,
) -> bytes:
    """生成 400x300 PNG 字节。

    优先用 InkSight 的渲染管线；如果它的依赖缺失（alimt 等），回退到最小自绘渲染器，
    保证 device 端永远能收到一帧图。
    """
    try:
        from backend.core.pipeline import generate_and_render  # type: ignore
        from backend.core.context import (  # type: ignore
            DEFAULT_CITY,
            get_date_context,
            get_weather_cached,
        )

        date_ctx = await get_date_context()
        weather = await get_weather_cached(city=DEFAULT_CITY)
        battery_pct = 88.0

        img, _ = await generate_and_render(
            persona=persona,
            config={},
            date_ctx=date_ctx,
            weather=weather,
            battery_pct=battery_pct,
            screen_w=screen_w,
            screen_h=screen_h,
            mac=mac,
            colors=colors,
        )
        if img.size != (screen_w, screen_h):
            img = img.resize((screen_w, screen_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning("InkSight pipeline render failed (%s), using fallback renderer", e)
        return _fallback_render(persona, screen_w, screen_h)


def _fallback_render(persona: str, w: int, h: int) -> bytes:
    """最简自绘：白底黑字 + 标题/时间/人设。保证端到端链路永远能通。"""
    from datetime import datetime

    # PIL '1' 模式: 255=白(0xFF), 0=黑(0x00)
    img = Image.new("1", (w, h), 255)  # 255: clear the frame (官方代码)
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("arial.ttf", 36)
        font_med = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font_big = font_med = font_small = ImageFont.load_default()

    draw.text((10, 10), "InkSight + Waveshare 4.2", fill=0, font=font_big)
    draw.text((10, 60), f"Persona: {persona}", fill=0, font=font_med)
    draw.text((10, 100), f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", fill=0, font=font_med)
    draw.text((10, 140), "v28: official getbuffer", fill=0, font=font_small)
    draw.text((10, 170), "1bpp MSB-first, 0=white, 1=black", fill=0, font=font_small)

    # 边框 + 十字线 (容易看清是否对)
    draw.rectangle((0, 0, w - 1, h - 1), outline=0)
    draw.line((0, 0, w - 1, h - 1), fill=0)
    draw.line((0, h - 1, w - 1, 0), fill=0)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ==================== HTTP 控制端点 ====================

class Bridge:
    DEFAULT_PERSONA = "DAILY"  # 用最简单、不依赖外部翻译/数据库的模式
    DEFAULT_OTA_BIN = r"d:\Hardware\esp32\cloudModule\firmware_merged.bin"

    def __init__(self, device: WaveshareDevice):
        self.device = device
        self.last_push: Optional[dict] = None
        self.latest_bw: Optional[bytes] = None
        self.latest_persona: Optional[str] = None
        # sleep_after_push: 设 True 时, 下一次推图后追加 ;S/ 让设备关机省电
        # 每次取出后自动重置为 False (一次性, 类似 ota_armed)
        self.sleep_after_push: bool = False
        # OTA 状态: armed=True 时, 设备下次连入就推 .bin
        self.ota_armed: bool = False
        self.ota_path: Optional[str] = None
        self.ota_history: list[dict] = []
        # OTA 实时进度 (供 webapp 5s 轮询 /ota/progress)
        self.ota_progress: dict = {
            "phase": "idle",   # idle | armed | handshake | ota_entry | pushing | finishing | done | error | cancelled
            "bytes_sent": 0,
            "bytes_total": 0,
            "chunks_done": 0,
            "chunks_total": 0,
            "percent": 0.0,
            "ts": 0.0,
        }

    def push_frame(self, frame_bytes: bytes) -> str:
        return self.device.push_image_and_refresh(frame_bytes)

    async def warm_up(self, persona: Optional[str] = None) -> dict:
        """启动时调一次后端渲染，缓存一帧 1bpp 数据到 self.latest_bw。
        设备首次连进来时立即能拿到，避免它 timeout。
        """
        persona = persona or self.DEFAULT_PERSONA
        png_bytes = await render_persona_to_png(persona)
        bw = png_bytes_to_1bpp(png_bytes)
        self.latest_bw = bw
        self.latest_persona = persona
        return {
            "persona": persona,
            "png_bytes": len(png_bytes),
            "bw_bytes": len(bw),
            "cached": True,
        }

    async def push_persona(self, persona: str) -> dict:
        t0 = time.time()
        png_bytes = await render_persona_to_png(persona)
        t_render = time.time() - t0

        bw = png_bytes_to_1bpp(png_bytes)
        t_dither = time.time() - t0 - t_render
        self.latest_bw = bw  # 缓存给被动模式

        resp = self.push_frame(bw)
        t_total = time.time() - t0

        result = {
            "persona": persona,
            "png_bytes": len(png_bytes),
            "bw_bytes": len(bw),
            "render_ms": int(t_render * 1000),
            "dither_ms": int(t_dither * 1000),
            "total_ms": int(t_total * 1000),
            "device_response": resp,
        }
        self.last_push = result
        return result


def make_app(device: WaveshareDevice) -> FastAPI:
    bridge = Bridge(device)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # noqa: ANN202
        logger.info("bridge ready, device=%s:%s", device.host, device.port)

        # 启动前先初始化 InkSight 的静态表（POETRY 等模式需要 device_state 表）
        try:
            from backend.core import static_store  # type: ignore
            await static_store.init_static_tables()
            logger.info("static tables initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("static table init failed: %s", e)

        # 启动时预先渲染+缓存一帧，避免设备首次连进来时延迟
        try:
            warm = await bridge.warm_up()
            logger.info("warm-up ok: persona=%s, bw=%d bytes", warm["persona"], warm["bw_bytes"])
        except Exception as e:  # noqa: BLE001
            logger.warning("warm-up failed (will retry on first connection): %s", e)

        # 启动被动监听：设备作为 TCP 客户端连进来时，自动推一帧
        import threading

        stop_event = threading.Event()

        def _ota_provider() -> Optional[str]:
            if bridge.ota_armed and bridge.ota_path:
                # 一次性: 取出后立即 disarm, 防止下次连接再推
                p = bridge.ota_path
                bridge.ota_armed = False
                bridge.ota_path = None
                # 重置 progress, 标记开始
                try:
                    bridge.ota_progress = {
                        "phase": "handshake",
                        "bytes_sent": 0,
                        "bytes_total": os.path.getsize(p),
                        "chunks_done": 0,
                        "chunks_total": 0,
                        "percent": 0.0,
                        "ts": time.time(),
                    }
                except OSError:
                    pass
                return p
            return None

        # ota_stream v29 期望 progress_callback: Callable[[dict], None]
        # bridge 端把 progress 写到 bridge.ota_progress 共享状态供 webapp 轮询
        def _ota_progress_writer(d: dict) -> None:
            bridge.ota_progress.update(d)

        def _sleep_provider() -> bool:
            """每次取后立即消费, 防止持续睡眠."""
            if bridge.sleep_after_push:
                bridge.sleep_after_push = False
                logger.info("[push] sleep_provider TRIGGERED -> will send ;S/ after push")
                return True
            return False

        start_passive_server(
            host="0.0.0.0",
            port=device.port,
            image_provider=lambda: bridge.latest_bw or b"\xff" * WAVESHARE_42_FRAME_BYTES,
            stop_event=stop_event,
            ota_provider=_ota_provider,
            ota_progress_provider=_ota_progress_writer,  # 直接是 Callable[[dict], None]
            sleep_provider=_sleep_provider,
        )
        logger.info("passive server started on 0.0.0.0:%d", device.port)
        try:
            yield
        finally:
            stop_event.set()

    app = FastAPI(title="Waveshare Bridge", lifespan=lifespan)

    @app.get("/status")
    async def status():
        return {
            "device": {"host": device.host, "port": device.port},
            "screen": {"w": WAVESHARE_42_SCREEN_W, "h": WAVESHARE_42_SCREEN_H},
            "last_push": bridge.last_push,
            "latest_persona": bridge.latest_persona,
            "latest_bw_cached": bridge.latest_bw is not None,
            "latest_bw_len": len(bridge.latest_bw) if bridge.latest_bw else 0,
        }

    @app.get("/device/ping")
    async def device_ping():
        try:
            resp = device.ping()
            return {"ok": True, "response": resp}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"device ping failed: {e}")

    @app.post("/push")
    async def push(payload: dict):
        """渲染 + 1bpp + 缓存到内存. 设备下次连进来时被动下发.

        active push (尝试主动连设备) 在后台 fire-and-forget, 不阻塞响应.

        payload: {"persona": "DAILY", "sleep_after": true (可选)}
          sleep_after=true: 推图后让设备进入 ;S/ 关机省电 (一次性, 下次 push 自动重置)
        """
        persona = payload.get("persona")
        if not persona:
            raise HTTPException(400, "persona is required")
        sleep_after = bool(payload.get("sleep_after", False))

        # 1) 同步: 渲染 + 缓存. 这一步必须成功, 否则 persona 不存在 / LLM 炸了.
        t0 = time.time()
        try:
            png_bytes = await render_persona_to_png(persona)
        except Exception as e:  # noqa: BLE001
            logger.exception("render failed for %s", persona)
            raise HTTPException(500, f"render failed: {e}")

        t_render = time.time() - t0
        bw = png_bytes_to_1bpp(png_bytes)
        t_dither = time.time() - t0 - t_render

        bridge.latest_bw = bw
        bridge.latest_persona = persona
        bridge.last_push = {
            "persona": persona,
            "png_bytes": len(png_bytes),
            "bw_bytes": len(bw),
            "render_ms": int(t_render * 1000),
            "dither_ms": int(t_dither * 1000),
            "ts": time.time(),
        }
        # 设一次性 sleep 标志: 设备下次连入推完图后追加 ;S/
        if sleep_after:
            bridge.sleep_after_push = True
            logger.info("[push] sleep_after_push ARMED (device will sleep after next push)")
        logger.info("[push] cached %s: png=%d bw=%d render=%dms dither=%dms",
                    persona, len(png_bytes), len(bw), int(t_render * 1000), int(t_dither * 1000))

        # 2) 异步: 尝试主动推图. 设备不在线就超时, 不影响响应.
        async def _try_active_push() -> None:
            try:
                resp = await asyncio.to_thread(bridge.push_frame, bw)
                logger.info("[push] active push %s: %s", persona, resp[:120])
            except Exception as e:  # noqa: BLE001
                logger.info("[push] active push %s skipped (no device): %s", persona, e)

        asyncio.create_task(_try_active_push())

        return {
            "ok": True,
            "persona": persona,
            "png_bytes": len(png_bytes),
            "bw_bytes": len(bw),
            "render_ms": int(t_render * 1000),
            "dither_ms": int(t_dither * 1000),
            "cached": True,
            "sleep_after": sleep_after,
            "next_push": "device will receive on next TCP connection (passive mode)",
        }

    @app.get("/preview/{persona}")
    async def preview(persona: str):
        """返回 400x300 PNG（不下发到设备），用于本地预览。"""
        png = await render_persona_to_png(persona)
        return Response(content=png, media_type="image/png")

    @app.get("/modes")
    async def modes():
        """列出所有 InkSight 内置 JSON 模式（30 个）。

        从 backend/core/modes/builtin/*.json 扫描，交给 webapp 渲染下拉框。
        """
        import glob

        builtin_dir = os.path.join(BACKEND_DIR, "core", "modes", "builtin")
        out = []
        for path in sorted(glob.glob(os.path.join(builtin_dir, "*.json"))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            out.append(
                {
                    "mode_id": d.get("mode_id", os.path.splitext(os.path.basename(path))[0]).upper(),
                    "display_name": d.get("display_name", ""),
                    "icon": d.get("icon", "star"),
                    "cacheable": d.get("cacheable", True),
                    "description": d.get("description", ""),
                    "source": "builtin_json",
                    "file": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
                }
            )
        return {"count": len(out), "modes": out}

    @app.post("/push_all")
    async def push_all(payload: Optional[dict] = None):
        """按顺序把 30 个真实模式全部推一遍（每个 5s 墨水屏刷新）。

        payload: {"delay": 6.0, "personas": ["DAILY", "POETRY", ...] (可选子集)}
        失败不中断, 返回每条的 ok/err.
        """
        delay = float((payload or {}).get("delay", 6.0))
        personas = (payload or {}).get("personas")
        if not personas:
            import glob as _glob

            builtin_dir = os.path.join(BACKEND_DIR, "core", "modes", "builtin")
            personas = []
            for p in sorted(_glob.glob(os.path.join(builtin_dir, "*.json"))):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    personas.append(d.get("mode_id", "").upper())
                except Exception:  # noqa: BLE001
                    continue
            personas = [p for p in personas if p]

        results = []
        for i, p in enumerate(personas, 1):
            t0 = time.time()
            try:
                r = await bridge.push_persona(p)
                r["index"] = i
                r["elapsed_s"] = round(time.time() - t0, 2)
                results.append(r)
                logger.info("[push_all] %d/%d %s ok", i, len(personas), p)
                if i < len(personas):
                    await asyncio.sleep(delay)
            except Exception as e:  # noqa: BLE001
                results.append({"persona": p, "index": i, "ok": False, "error": str(e)})
                logger.exception("[push_all] %d/%d %s failed", i, len(personas), p)
        return {"total": len(personas), "results": results}

    # ==================== OTA 升级 ====================

    @app.get("/ota/status")
    async def ota_status():
        """当前 OTA 状态. armed=True 表示设备下次连入就开始推 .bin."""
        return {
            "armed": bridge.ota_armed,
            "path": bridge.ota_path,
            "history": bridge.ota_history[-5:],  # 最近 5 次
            "default_bin": Bridge.DEFAULT_OTA_BIN,
            "progress": dict(bridge.ota_progress),  # 当前进度 (兼容)
        }

    @app.post("/ota/arm")
    async def ota_arm(payload: Optional[dict] = None):
        """武装 OTA: 设备下次连入时, 推 .bin.

        payload: {"path": "D:\\\\...\\\\firmware_merged.bin"}  (可选, 默认用 Bridge.DEFAULT_OTA_BIN)
        """
        path = (payload or {}).get("path") or Bridge.DEFAULT_OTA_BIN
        path = os.path.normpath(path)
        if not os.path.exists(path):
            raise HTTPException(404, f"ota bin not found: {path}")
        fsize = os.path.getsize(path)
        if fsize == 0:
            raise HTTPException(400, f"ota bin is empty: {path}")

        bridge.ota_path = path
        bridge.ota_armed = True
        # 把 progress 标记为 armed, 让 webapp 显示橙色等待
        bridge.ota_progress = {
            "phase": "armed",
            "bytes_sent": 0,
            "bytes_total": fsize,
            "chunks_done": 0,
            "chunks_total": 0,
            "percent": 0.0,
            "ts": time.time(),
        }
        logger.warning("[ota] ARMED path=%s size=%d. 设备下次连入就会触发推 .bin, 不可撤销.", path, fsize)
        return {
            "ok": True,
            "armed": True,
            "path": path,
            "size": fsize,
            "warning": "设备下次连入就会开始刷写. 失败/中断会导致设备变砖.",
        }

    @app.post("/ota/cancel")
    async def ota_cancel():
        """取消武装的 OTA. 必须在上次 /ota/arm 之后、还没设备连入之前调用."""
        bridge.ota_armed = False
        bridge.ota_path = None
        # 把 phase 标记为 cancelled (如果之前是 armed/idle)
        if bridge.ota_progress.get("phase") not in ("done", "error"):
            bridge.ota_progress["phase"] = "cancelled"
        logger.info("[ota] cancelled")
        return {"ok": True, "armed": False}

    @app.get("/ota/progress")
    async def ota_progress():
        """OTA 实时进度 (webapp 5s 轮询).

        Returns:
          phase: idle | armed | handshake | ota_entry | pushing | finishing | done | error | cancelled
          bytes_sent / bytes_total
          chunks_done / chunks_total
          percent: 0-100
          ts: 时间戳
        """
        p = dict(bridge.ota_progress)  # copy, 防 webapp 读到半更新状态
        # 加上 arms 状态, 方便 webapp 一次拿全
        p["armed"] = bridge.ota_armed
        p["path"] = bridge.ota_path
        # 距上次更新的秒数 (webapp 用来判断进度是否停滞)
        p["age_s"] = round(time.time() - p.get("ts", 0), 1) if p.get("ts") else None
        return p

    return app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-ip", default=os.environ.get("WAVESHARE_DEVICE_IP", "192.168.4.1"))
    ap.add_argument("--device-port", type=int, default=int(os.environ.get("WAVESHARE_DEVICE_PORT", "6868")))
    ap.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "9000")))
    args = ap.parse_args()

    device = WaveshareDevice(
        host=args.device_ip,
        port=args.device_port,
        password=os.environ.get("WAVESHARE_DEVICE_PASSWORD", ""),
    )

    # 启动前先 ping 一下，失败不阻塞（让用户也能改 IP）
    try:
        logger.info("ping device %s:%s ...", device.host, device.port)
        logger.info("device response: %r", device.ping())
    except Exception as e:  # noqa: BLE001
        logger.warning("device ping failed (will keep running): %s", e)

    app = make_app(device)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
