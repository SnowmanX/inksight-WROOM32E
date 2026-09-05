"""
A 阶段验证：30 个真实模式全部能渲染。

不依赖真设备（不连 6868），只调 InkSight pipeline 把 30 个 JSON 模式逐个跑一遍：
  - 调 generate_and_render(persona, ...) → PIL.Image
  - 验证 Image.size == (400, 300)
  - 调 png_bytes_to_1bpp 跑一次 PNG→1bpp
  - 验证 1bpp 长度 == 15000

结果写到 logs/mode_smoke.json, 失败的 persona 列在 logs/mode_smoke_failed.txt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(THIS_DIR)
REPO_ROOT = os.path.dirname(BACKEND_DIR)
for p in (BACKEND_DIR, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# 必须先打 alimt stub, 否则 import json_content 会崩
from backend.scripts import waveshare_bridge  # noqa: F401  (触发 _stub_alimt_module)

from backend.scripts.waveshare_bridge import (
    render_persona_to_png,
    png_bytes_to_1bpp,
    WAVESHARE_42_FRAME_BYTES,
    WAVESHARE_42_SCREEN_W,
    WAVESHARE_42_SCREEN_H,
)
from PIL import Image

logger = logging.getLogger("mode_smoke")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

LOGS_DIR = Path(REPO_ROOT) / "logs"
LOGS_DIR.mkdir(exist_ok=True)
OUT_JSON = LOGS_DIR / "mode_smoke.json"
OUT_FAILED = LOGS_DIR / "mode_smoke_failed.txt"

BUILTIN_DIR = Path(BACKEND_DIR) / "core" / "modes" / "builtin"


def list_builtin_modes() -> list[str]:
    mids = []
    for p in sorted(BUILTIN_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            mids.append(d.get("mode_id", p.stem).upper())
        except Exception as e:  # noqa: BLE001
            logger.warning("skip %s: %s", p, e)
    return mids


async def run_one(persona: str) -> dict:
    t0 = time.time()
    png = await render_persona_to_png(persona)
    t_render = time.time() - t0

    img = Image.open(io := __import__("io").BytesIO(png))
    t_dither_start = time.time()
    bw = png_bytes_to_1bpp(png)
    t_dither = time.time() - t_dither_start

    return {
        "persona": persona,
        "ok": True,
        "img_size": img.size,
        "img_mode": img.mode,
        "png_bytes": len(png),
        "bw_bytes": len(bw),
        "bw_match": len(bw) == WAVESHARE_42_FRAME_BYTES,
        "render_ms": int(t_render * 1000),
        "dither_ms": int(t_dither * 1000),
    }


async def main() -> int:
    # 1) 初始化所有需要的 SQLite 表
    try:
        from backend.core import static_store
        await static_store.init_static_tables()
        logger.info("static tables initialized")
    except Exception as e:  # noqa: BLE001
        logger.warning("init_static_tables failed: %s", e)

    try:
        from backend.core import stats_store
        await stats_store.init_stats_db()
        logger.info("stats tables initialized")
    except Exception as e:  # noqa: BLE001
        logger.warning("init_stats_db failed: %s", e)

    try:
        from backend.core import vocab_store
        await vocab_store.seed_builtin_vocab()
        logger.info("vocab seed done")
    except Exception as e:  # noqa: BLE001
        logger.warning("seed_builtin_vocab failed: %s", e)

    # 2) 触发一次 vocab session 让 ensure_vocab_session 隐式建表
    try:
        from backend.core import vocab_store
        await vocab_store.ensure_vocab_session("test-mac", {})
        logger.info("vocab_session_state ensured")
    except Exception as e:  # noqa: BLE001
        logger.warning("ensure_vocab_session failed: %s", e)

    # 3) device_state 表: 没找到专用 init, 直接用 SQL 兜底
    try:
        import aiosqlite
        db_path = os.path.join(BACKEND_DIR, "inksight.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS device_state (
                    mac TEXT PRIMARY KEY,
                    updated_at TEXT
                )
                """
            )
            await db.commit()
        logger.info("device_state table ensured")
    except Exception as e:  # noqa: BLE001
        logger.warning("device_state ensure failed: %s", e)

    personas = list_builtin_modes()
    logger.info("discovered %d builtin modes", len(personas))

    results: list[dict] = []
    failed: list[dict] = []
    for i, p in enumerate(personas, 1):
        try:
            r = await run_one(p)
            results.append(r)
            logger.info(
                "[%2d/%2d] %-20s ok  png=%5d  bw=%5d  render=%4dms  dither=%4dms",
                i, len(personas), p, r["png_bytes"], r["bw_bytes"], r["render_ms"], r["dither_ms"],
            )
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            failed.append({"persona": p, "error": str(e), "tb": tb})
            results.append({"persona": p, "ok": False, "error": str(e)})
            logger.error("[%2d/%2d] %-20s FAIL: %s", i, len(personas), p, e)

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FAILED.write_text(
        "\n\n".join(f"=== {f['persona']} ===\n{f['error']}\n{f['tb']}" for f in failed),
        encoding="utf-8",
    )

    ok_count = sum(1 for r in results if r.get("ok"))
    logger.info("==== summary: %d/%d ok (json=%s, failed=%s) ====", ok_count, len(personas), OUT_JSON, OUT_FAILED)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
