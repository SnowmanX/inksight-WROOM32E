"""
Waveshare 4.2" e-Paper Cloud Module 被动监听模式（Server Mode）。

按官方 Cloud_WIN 源码（lib/tcp_server/tcp_sver.py）：
- safe_send 重发直到设备 ACK
- F/ 后 sleep 0.1s
- D/ 后 sleep 5s 等墨水屏全刷
- 设备默认密码 123456
- O/ 为 OTA 升级模式（推 .bin 后 0x31 结束）
"""

from __future__ import annotations

import logging
import math
import os
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


def push_image(sock: socket.socket, image_bytes: bytes, password: str = DEFAULT_PASSWORD, sleep_after: bool = False, clear_before: bool = False) -> str:
    """v27: 完全模仿官方 Cloud_WIN 流程.

    流程: G/ → C/ → N<pw>/ → F/ → sleep 0.1s → 15帧 → closeFrame → D/ → sleep 5s
    可选: sleep_after=True 时, D/ 之后追加 ;S/ (设备关机省电)
    可选: clear_before=True 时, 先推一帧全白清屏 (消除残影), 再推主图
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

    if clear_before:
        # 先推一帧全白清屏, 强制 e-paper 做一次全像素 white 循环, 消除 ghosting.
        # 全白 1bpp = 全 0x00 (MSB-first, 0=白). F/ 之后用同一数据通道再发一遍即可.
        white_frame = b"\x00" * WAVESHARE_42_FRAME_BYTES
        log.append("CLEAR_BEFORE: pushing all-white frame to remove ghosting")
        logger.info("[push] CLEAR_BEFORE: sending all-white frame (15000 bytes 0x00)")
        clear_log = _send_one_image(sock, white_frame)
        log.append(f"CLEAR: {clear_log}")
        # 清屏帧已发完, 需要重新进 F/ 才能发第二张图 (设备协议要求)
        sock.sendall(build_cmd("F"))
        log.append(f"F/[after-clear]={_read_drain(sock, 3.0)!r}")
        time.sleep(0.1)

    # 5) 数据帧 (按真实字节长度, 最后一帧 664 字节) + close + D/
    log.append(_send_one_image(sock, image_bytes))

    # 8) 可选: ;S/ 让设备关机省电 (刷完才睡, 不会打断刷屏)
    if sleep_after:
        try:
            logger.info("[push] sending ;S/ to %s (sleep_after=True)", sock.getpeername())
            sock.sendall(build_cmd("S"))
            s_resp = _read_drain(sock, 3.0)
            log.append(f"S/={s_resp!r} (sleep after push)")
            logger.info("[push] ;S/ sent, device_response=%r", s_resp)
        except OSError as e:
            log.append(f"S/=<send_err:{e}> (device may have already disconnected)")
            logger.warning("[push] ;S/ send failed: %s", e)

    return " | ".join(log)


def _send_one_image(sock: socket.socket, image_bytes: bytes) -> str:
    """发送 15 帧 + closeFrame + ;D/ + 等 5s. 假定 F/ 已发且 sleep 0.1s 已做."""
    if len(image_bytes) != WAVESHARE_42_FRAME_BYTES:
        raise ValueError(f"image must be {WAVESHARE_42_FRAME_BYTES} bytes (got {len(image_bytes)})")
    log = []
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
    # closeFrame
    sock.sendall(build_close_frame())
    log.append(f"closeFrame={_read_drain(sock, 5.0)!r}")
    # ;D/ 触发刷屏
    sock.sendall(build_cmd("D"))
    d_resp = _read_drain(sock, 3.0)
    log.append(f"D/={d_resp!r}")
    time.sleep(5.0)  # 关键: 等墨水屏全刷 (4-5s)
    return " | ".join(log)


# 兼容旧名字
def push_image_v23(sock: socket.socket, image_bytes: bytes, password: str = DEFAULT_PASSWORD) -> str:
    return push_image(sock, image_bytes, password)


# ==================== OTA 模式 ====================

# 云端用这个简化版的 safe_send: 设备不回 cs 字节, 但我们不阻塞, 1.5s 内放弃
# 官方版是 while True + sleep(1) 重发, 我们用超时控制, 设备如果没回应就报错
def _safe_send_ota(sock: socket.socket, payload: bytes, expected_ack: bytes, timeout: float = 5.0) -> str:
    """Send once, wait for expected_ack echo. Returns the actual ack bytes received."""
    sock.settimeout(timeout)
    try:
        sock.sendall(payload)
        chunk = sock.recv(64)
        if not chunk:
            return "<no-ack>"
        return chunk.hex()
    except socket.timeout:
        return "<timeout>"
    except Exception as e:
        return f"<err:{e}>"


def ota_stream(
    sock: socket.socket,
    bin_path: str,
    *,
    chunk_size: int = 256,
    inter_chunk_delay: float = 0.05,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> str:
    """v29: 仿 Cloud_WIN ota() 流程 + 实时进度回调.

    流程: G/ → C/ → N/ → O/ → chunks → 0x31 结束.
    改进 (vs v28):
      - chunk_size 默认 256 字节 (was 1024), 给设备 flash 写入留时间
      - 每个 chunk 后 sleep 50ms, 避免 TCP buffer 溢出 / 设备断连
      - 每个 chunk 调 progress_callback 让 webapp 显示实时百分比

    progress_callback 收到的 dict:
      {"phase": "handshake"|"ota_entry"|"pushing"|"finishing"|"done"|"error",
       "bytes_sent": int, "bytes_total": int, "chunks_done": int, "chunks_total": int,
       "percent": float, "ts": float}
    """
    if not os.path.exists(bin_path):
        raise FileNotFoundError(f"ota bin not found: {bin_path}")
    fsize = os.path.getsize(bin_path)
    if fsize == 0:
        raise ValueError(f"ota bin is empty: {bin_path}")
    log = []
    t_start = time.time()

    def _report(phase: str, **kw) -> None:
        if progress_callback:
            try:
                progress_callback({
                    "phase": phase,
                    "bytes_sent": kw.get("bytes_sent", 0),
                    "bytes_total": fsize,
                    "chunks_done": kw.get("chunks_done", 0),
                    "chunks_total": n_chunks if "n_chunks" in dir() else 0,
                    "percent": round(100 * kw.get("bytes_sent", 0) / max(fsize, 1), 1),
                    "ts": time.time(),
                })
            except Exception as cb_err:  # noqa: BLE001
                logger.warning("[ota] progress_callback err: %s", cb_err)

    _report("handshake", bytes_sent=0)

    # 1) 复用标准握手
    sock.sendall(build_cmd("G"))
    log.append(f"G/={_read_drain(sock, 3.0)!r}")
    sock.sendall(build_cmd("C"))
    log.append(f"C/={_read_drain(sock, 3.0)!r}")
    sock.sendall(build_cmd("N", DEFAULT_PASSWORD.encode("ascii")))
    log.append(f"N/={_read_drain(sock, 3.0)!r}")

    # 2) ;O/ 进 OTA 模式
    ack = _safe_send_ota(sock, build_cmd("O"), bytes([0x4F]), timeout=5.0)
    log.append(f"O/={ack!r}")
    if "err" in ack or ack == "<timeout>" or ack == "<no-ack>":
        _report("error")
        raise RuntimeError(f"device rejected OTA entry (ack={ack!r})")
    _report("ota_entry", bytes_sent=0)

    # 3) 流式推 .bin (默认 256 字节/帧, 慢速)
    logger.info("[ota] %s starting push: fsize=%d chunk_size=%d delay=%.3fs",
                bin_path, fsize, chunk_size, inter_chunk_delay)
    n_chunks = math.ceil(fsize / chunk_size)
    sent = 0
    with open(bin_path, "rb") as f:
        for i in range(n_chunks):
            c = f.read(chunk_size)
            if not c:
                break
            try:
                sock.sendall(c)
            except OSError as e:
                _report("error", bytes_sent=sent, chunks_done=i)
                raise RuntimeError(f"socket send failed at chunk {i + 1}/{n_chunks} (sent {sent}/{fsize} B): {e}")
            sent += len(c)
            if (i + 1) % 64 == 0 or (i + 1) == n_chunks:
                logger.info("[ota] progress: %d / %d chunks, %d / %d bytes (%.1f%%)",
                            i + 1, n_chunks, sent, fsize, 100 * sent / fsize)
                _report("pushing", bytes_sent=sent, chunks_done=i + 1)
            if inter_chunk_delay > 0:
                time.sleep(inter_chunk_delay)
    log.append(f"chunks={n_chunks} sent={sent}/{fsize}")

    # 4) 0x31 结束标志
    _report("finishing", bytes_sent=sent, chunks_done=n_chunks)
    sock.sendall(b"1")
    log.append("end=0x31 sent")

    # 5) 等设备 reboot 完
    time.sleep(2.0)
    log.append("sleep 2s done")
    _report("done", bytes_sent=sent, chunks_done=n_chunks)

    logger.info("[ota] DONE: %d bytes in %.1fs", sent, time.time() - t_start)
    return " | ".join(log)


# ==================== Connection handler ====================


def handle_one_connection(
    sock: socket.socket,
    addr: tuple,
    image_provider: Callable[[], bytes],
    *,
    ota_provider: Optional[Callable[[], Optional[str]]] = None,
    ota_progress_provider: Optional[Callable[[Callable[[dict], None]], None]] = None,
    sleep_provider: Optional[Callable[[], bool]] = None,
    clear_provider: Optional[Callable[[], bool]] = None,
) -> None:
    peer = f"{addr[0]}:{addr[1]}"
    logger.info("[passive] device connected: %s", peer)
    sock.settimeout(15.0)
    try:
        # 检查本次连接是否要走 OTA
        ota_path = ota_provider() if ota_provider else None
        if ota_path:
            logger.info("[passive] OTA mode activated for %s -> %s", peer, ota_path)
            # 让 bridge 注入 progress 回调 (写到共享状态供 webapp 轮询)
            _cb = None
            if ota_progress_provider:
                def _cb(d: dict) -> None:
                    ota_progress_provider(d)
            result = ota_stream(sock, ota_path, progress_callback=_cb)
            logger.info("[passive v29 OTA] %s DONE: %s", peer, result)
            return

        try:
            bw = image_provider()
            if not bw or len(bw) != WAVESHARE_42_FRAME_BYTES:
                logger.error("[passive] image_provider invalid (len=%s)", len(bw) if bw else 0)
                return
        except Exception as e:
            logger.exception("[passive] image_provider failed: %s", e)
            return

        # sleep_provider / clear_provider 只对真实设备 IP 触发 (过滤 loopback / 主动推失败的重试连接)
        # 关键: 先判 real_device 再消费标志, 否则 loopback 连接会先吞掉一次性标志,
        # 真正设备来时反而拿不到 True, ;S/ / 清屏永远发不出.
        is_real_device = addr[0] not in ("127.0.0.1", "::1") and addr[0] != sock.getsockname()[0]
        sleep_after = False
        clear_before = False
        if is_real_device:
            if sleep_provider:
                sleep_after = bool(sleep_provider())
                if sleep_after:
                    logger.info("[passive] real device %s, sleep_provider TRIGGERED -> ;S/ after push", peer)
            if clear_provider:
                clear_before = bool(clear_provider())
                if clear_before:
                    logger.info("[passive] real device %s, clear_provider TRIGGERED -> all-white frame before push", peer)
        result = push_image(sock, bw, sleep_after=sleep_after, clear_before=clear_before)
        logger.info("[passive v27] %s DONE (sleep_after=%s, clear_before=%s, real_device=%s): %s",
                    peer, sleep_after, clear_before, is_real_device, result)
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
    ota_provider: Optional[Callable[[], Optional[str]]] = None,
    ota_progress_provider: Optional[Callable[[Callable[[dict], None]], None]] = None,
    sleep_provider: Optional[Callable[[], bool]] = None,
    clear_provider: Optional[Callable[[], bool]] = None,
) -> threading.Thread:
    def _serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(5)
        srv.settimeout(1.0)
        logger.info("[passive v29] listening on %s:%d", host, port)
        try:
            while not (stop_event and stop_event.is_set()):
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(
                    target=handle_one_connection,
                    args=(conn, addr, image_provider),
                    kwargs={"ota_provider": ota_provider, "ota_progress_provider": ota_progress_provider,
                            "sleep_provider": sleep_provider, "clear_provider": clear_provider},
                    daemon=True,
                )
                t.start()
        finally:
            srv.close()
            logger.info("[passive v29] server closed")

    th = threading.Thread(target=_serve, daemon=True, name="waveshare-passive-v29")
    th.start()
    return th
