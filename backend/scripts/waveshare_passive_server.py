"""
Waveshare 4.2" e-Paper Cloud Module 被动监听模式（Server Mode）。

按官方 Cloud_WIN 源码（lib/tcp_server/tcp_sver.py）：
- safe_send 重发直到设备 ACK
- F/ 后 sleep 0.1s
- D/ 后 sleep 5s 等墨水屏全刷
- 设备默认密码 123456
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, Optional

from .waveshare_protocol import (
    WAVESHARE_42_FRAME_BYTES,
    build_cmd,
    build_data,
    build_close_frame,
)

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "123456"


def _read_drain(sock: socket.socket, timeout: float = 1.0) -> str:
    """读 1 次响应 + drain 残留 echo（防 TCP 粘包）。"""
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = sock.recv(64)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) >= 5:
                sock.settimeout(0.2)
                try:
                    extra = sock.recv(64)
                    if extra:
                        chunks.append(extra)
                except socket.timeout:
                    pass
                break
    except socket.timeout:
        pass
    except Exception:
        pass
    if not chunks:
        return "<timeout>"
    return "".join(c.hex() for c in chunks)


def push_image(sock: socket.socket, image_bytes: bytes, password: str = DEFAULT_PASSWORD) -> str:
    """v27: 完全模仿官方 Cloud_WIN 流程。

    流程: G/ → C/ → N<pw>/ → F/ → sleep 0.1s → 15帧 → closeFrame → D/ → sleep 5s
    """
    if len(image_bytes) != WAVESHARE_42_FRAME_BYTES:
        raise ValueError(f"image must be {WAVESHARE_42_FRAME_BYTES} bytes (got {len(image_bytes)})")

    log = []

    # 1) ;G/ Get ID
    sock.sendall(build_cmd("G"))
    log.append(f"G/={_read_drain(sock, 3.0)!r}")

    # 2) ;C/ 查锁
    sock.sendall(build_cmd("C"))
    log.append(f"C/={_read_drain(sock, 3.0)!r}")

    # 3) ;N<pw>/ 解锁
    sock.sendall(build_cmd("N", password.encode("ascii")))
    log.append(f"N/={_read_drain(sock, 3.0)!r}")

    # 4) ;F/ 进数据模式 + sleep 0.1s
    sock.sendall(build_cmd("F"))
    log.append(f"F/={_read_drain(sock, 3.0)!r}")
    time.sleep(0.1)  # 官方: time.sleep(0.1) 在 F/ 之后

    # 5) 数据帧 (按真实字节长度, 最后一帧 664 字节)
    # 15000 = 14*1024 + 664
    addr = 0
    frame_no = 0
    while addr < WAVESHARE_42_FRAME_BYTES:
        chunk_len = min(1024, WAVESHARE_42_FRAME_BYTES - addr)
        chunk = image_bytes[addr : addr + chunk_len]
        num = (frame_no % 4)
        sock.sendall(build_data(addr, chunk, num=num))
        log.append(f"frame{frame_no+1}/{15}({num},addr={addr},len={chunk_len})={_read_drain(sock, 3.0)!r}")
        addr += chunk_len
        frame_no += 1

    # 6) closeFrame (收尾)
    sock.sendall(build_close_frame())
    log.append(f"closeFrame={_read_drain(sock, 5.0)!r}")

    # 7) ;D/ 触发刷屏 + sleep 5s 等设备完成
    sock.sendall(build_cmd("D"))
    d_resp = _read_drain(sock, 3.0)
    log.append(f"D/={d_resp!r}")
    time.sleep(5.0)  # 关键: 等墨水屏全刷 (4-5s)

    return " | ".join(log)


# 兼容旧名字
def push_image_v23(sock: socket.socket, image_bytes: bytes, password: str = DEFAULT_PASSWORD) -> str:
    return push_image(sock, image_bytes, password)


def handle_one_connection(
    sock: socket.socket,
    addr: tuple,
    image_provider: Callable[[], bytes],
) -> None:
    peer = f"{addr[0]}:{addr[1]}"
    logger.info("[passive] device connected: %s", peer)
    sock.settimeout(15.0)
    try:
        try:
            bw = image_provider()
            if not bw or len(bw) != WAVESHARE_42_FRAME_BYTES:
                logger.error("[passive] image_provider invalid (len=%s)", len(bw) if bw else 0)
                return
        except Exception as e:
            logger.exception("[passive] image_provider failed: %s", e)
            return

        result = push_image(sock, bw)
        logger.info("[passive v27] %s DONE: %s", peer, result)
    except Exception as e:
        logger.warning("[passive] %s push failed: %s", peer, e)
        logger.exception(e)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def start_passive_server(
    host: str,
    port: int,
    image_provider: Callable[[], bytes],
    *,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    def _serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(5)
        srv.settimeout(1.0)
        logger.info("[passive v27] listening on %s:%d", host, port)
        try:
            while not (stop_event and stop_event.is_set()):
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(
                    target=handle_one_connection,
                    args=(conn, addr, image_provider),
                    daemon=True,
                )
                t.start()
        finally:
            srv.close()
            logger.info("[passive v27] server closed")

    th = threading.Thread(target=_serve, daemon=True, name="waveshare-passive-v27")
    th.start()
    return th
