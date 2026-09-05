"""
Fake waveshare device server (for testing the bridge as a CLIENT).

Listens on 6868, prints every byte the client sends, and replies with the
exact 5-byte echo that the real device returns: $cmd#\\x00\\x00 for cmd-mode
frames, and nothing for data frames.

Usage:
    python backend/scripts/fake_waveshare_device.py [port] [password]
"""
import socket
import sys
import threading
import time
from datetime import datetime

PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "0000"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 6868

LOG = open("d:/Hardware/esp32/cloudModule/inksight-waveshare-cloud-module/backend/fake_device.log", "w", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    s = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n"
    LOG.write(s)
    # 不再 print — PowerShell 缓冲 stdout 干扰


def _xor(buf: bytes) -> int:
    cs = 0
    for b in buf:
        cs ^= b
    return cs & 0xFF


def handle(conn: socket.socket, addr: tuple) -> None:
    peer = f"{addr[0]}:{addr[1]}"
    log(f"client connected: {peer}")
    conn.settimeout(15.0)
    try:
        buf = b""
        total_data = 0
        frame_count = 0
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                log(f"  [{peer}] recv timeout, closing")
                break
            if not chunk:
                log(f"  [{peer}] client closed")
                break
            buf += chunk
            total_data += len(chunk)
            log(f"  [{peer}] recv {len(chunk)}B (total={total_data}) hex={chunk.hex()[:80]}...")

            # 尝试解析帧
            while len(buf) > 0:
                # 指令模式: ;CMD../
                if buf[0:1] == b";":
                    end = buf.find(b"/")
                    if end < 0:
                        break  # 还没收完整
                    frame = bytes(buf[: end + 1])
                    cs = buf[end + 1] if end + 1 < len(buf) else None
                    cmd = frame[1:-1].decode("ascii", errors="replace")
                    log(f"  [{peer}] CMD: {cmd!r} cs={cs}")
                    # echo: $cmd#\x00\x00
                    echo = b"$" + frame[1:-1] + b"#\x00\x00"
                    conn.sendall(echo)
                    log(f"  [{peer}] echo {len(echo)}B: {echo.hex()}")
                    buf = buf[end + 2 :]  # skip frame + cs
                    continue

                # 数据模式: 0x57 + 4B addr + 4B len + 1B num
                if buf[0:1] == b"\x57":
                    if len(buf) < 10:
                        break
                    addr_bytes = buf[1:5]
                    len_bytes = buf[5:9]
                    num = buf[9]
                    data_len = int.from_bytes(len_bytes, "big")
                    total_len = 10 + data_len + 1  # +1 for checksum
                    if len(buf) < total_len:
                        log(f"  [{peer}] data frame incomplete: have {len(buf)}, need {total_len}")
                        break
                    addr = int.from_bytes(addr_bytes, "big")
                    data = bytes(buf[10 : 10 + data_len])
                    cs = buf[10 + data_len]
                    frame_count += 1
                    if data_len == 0:
                        log(f"  [{peer}] EOT (refresh) addr={addr} num={num}")
                        # 收尾帧 → 模拟"开始刷屏"等待 4s
                        log(f"  [{peer}] *** FAKE DISPLAY REFRESH *** (would take 4s on real device)")
                        time.sleep(2)
                        log(f"  [{peer}] *** DISPLAY REFRESHED ***")
                        # 真实设备对 EOT 帧回 0 字节或 5 字节 echo，我们回 5 字节保持兼容
                        conn.sendall(b"\x00" * 5)
                    else:
                        log(
                            f"  [{peer}] DATA FRAME #{frame_count}: addr={addr} len={data_len} "
                            f"first8={data[:8].hex()} last8={data[-8:].hex()}"
                        )
                        # 数据帧设备不回 ack（headblockhead 文档）— 我们也不回
                    buf = buf[total_len:]
                    continue

                log(f"  [{peer}] unknown frame head {buf[0:1].hex()}, dropping 1B")
                buf = buf[1:]
    except Exception as e:
        log(f"  [{peer}] err: {e}")
    finally:
        conn.close()
        log(f"  [{peer}] closed (total_data={total_data} frames={frame_count})")


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(5)
    log(f"FAKE device listening on 0.0.0.0:{PORT} (password={PASSWORD!r})")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
