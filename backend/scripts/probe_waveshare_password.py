"""探测微雪 4.2" Cloud Module 设备当前锁定状态和默认密码。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.scripts.waveshare_protocol import (
    build_cmd,
    read_response,
    send_and_read,
    DEFAULT_PORT,
)


def probe(host: str, port: int = DEFAULT_PORT, timeout: float = 2.0):
    import socket
    print(f"== Probe {host}:{port} ==")

    # 1) 检查锁定状态
    print("\n[1] 查锁定状态 ;C/")
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    try:
        send_and_read(s, build_cmd("C"), timeout=1.5)
        locked = read_response(s, timeout=1.5)
        print(f"    锁定? = {locked!r}")
    finally:
        s.close()

    # 2) 试常见密码
    candidates = ["", "0000", "1234", "password", "waveshare", "admin", "12345678", "00000000"]
    for pw in candidates:
        print(f"\n[2] 试解锁密码: {pw!r}")
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        try:
            send_and_read(s, build_cmd("C"), timeout=1.5)
            read_response(s, timeout=1.5)  # 锁定状态
            send_and_read(s, build_cmd("N", pw.encode("ascii")), timeout=2.0)
            ack2 = read_response(s, timeout=2.0)
            print(f"    ack2 = {ack2!r}")
            if ack2.strip().lower() in ("successful", "success", "ok", "0", ""):
                print(f"    *** 密码命中: {pw!r} ***")
                return pw
        except Exception as e:
            print(f"    err: {e}")
        finally:
            s.close()

    # 3) 也试试发个 ';' + 空 cmd 看响应
    print("\n[3] ping 基线")
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    try:
        send_and_read(s, build_cmd(""), timeout=1.5)
        ack2 = read_response(s, timeout=1.5)
        print(f"    empty cmd resp: {ack2!r}")
    finally:
        s.close()
    return None


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.46"
    pwd = probe(host)
    print(f"\n最终密码: {pwd!r}")
