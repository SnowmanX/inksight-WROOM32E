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

from fastapi import FastAPI, HTTPException
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
        logging.FileHandler(os.environ.get("BRIDGE_LOG_FILE", "waveshare_bridge.log"), encoding="utf-8"),
    ],
)


# ==================== 图像处理：PNG → 1bpp 400x300 ====================

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
    draw.rectangle((0, 0, w-1, h-1), outline=0)
    draw.line((0, 0, w-1, h-1), fill=0)
    draw.line((0, h-1, w-1, 0), fill=0)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
    draw.text((20, 70), f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')}", fill=255, font=font_med)
    draw.text((20, 100), f"Mode: {persona}", fill=255, font=font_med)
    draw.text((20, 130), f"Device: 192.168.1.46", fill=255, font=font_med)
    draw.text((20, 160), f"Source: inksight-waveshare-cloud-module", fill=255, font=font_small)
    draw.text((20, 200), "All systems OK. Bridge alive.", fill=255, font=font_med)

    # 简单边框
    draw.rectangle((0, 0, w - 1, h - 1), outline=255, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ==================== HTTP 控制端点 ====================

class Bridge:
    DEFAULT_PERSONA = "DAILY"  # 用最简单、不依赖外部翻译/数据库的模式

    def __init__(self, device: WaveshareDevice):
        self.device = device
        self.last_push: Optional[dict] = None
        self.latest_bw: Optional[bytes] = None
        self.latest_persona: Optional[str] = None

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
        start_passive_server(
            host="0.0.0.0",
            port=device.port,
            image_provider=lambda: bridge.latest_bw or b"\xff" * WAVESHARE_42_FRAME_BYTES,
            stop_event=stop_event,
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
        persona = payload.get("persona")
        if not persona:
            raise HTTPException(400, "persona is required")
        try:
            return await bridge.push_persona(persona)
        except Exception as e:  # noqa: BLE001
            logger.exception("push failed")
            raise HTTPException(500, f"push failed: {e}")

    @app.get("/preview/{persona}")
    async def preview(persona: str):
        """返回 400x300 PNG（不下发到设备），用于本地预览。"""
        png = await render_persona_to_png(persona)
        return PlainTextResponse(png.decode("latin1"), media_type="image/png")

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
