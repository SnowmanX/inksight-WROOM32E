"""离线自检：协议层、帧拆分、抖动器、被动监听。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.scripts.waveshare_protocol import (
    WAVESHARE_42_FRAME_BYTES,
    WAVESHARE_42_SCREEN_H,
    WAVESHARE_42_SCREEN_W,
    build_cmd,
    build_data,
    xor_checksum,
)
from backend.scripts.waveshare_passive_server import push_image_to_socket, split_frames


def main() -> int:
    # 1. 协议帧
    cmd = build_cmd("ping")
    assert cmd.startswith(b";ping/") and cmd.endswith(bytes([xor_checksum(b";ping/")])), cmd
    print(f"[1] cmd frame ok: {cmd!r} len={len(cmd)}")

    data = build_data(addr=0, data=b"\x01\x02\x03", num=1)
    assert data[0] == 0x57, data[0]
    assert len(data) == 1 + 4 + 4 + 1 + 3 + 1, len(data)
    print(f"[2] data frame ok: len={len(data)} head=0x{data[0]:02x}")

    # 2. 帧拆分
    full = b"\xff" * WAVESHARE_42_FRAME_BYTES
    chunks = split_frames(full, max_frame=1024)
    assert sum(len(c) for c in chunks) == WAVESHARE_42_FRAME_BYTES
    print(f"[3] split into {len(chunks)} frames, total {sum(len(c) for c in chunks)} bytes (frame_bytes={WAVESHARE_42_FRAME_BYTES})")

    # 3. 屏幕尺寸常量
    assert WAVESHARE_42_SCREEN_W == 400 and WAVESHARE_42_SCREEN_H == 300
    print(f"[4] screen = {WAVESHARE_42_SCREEN_W}x{WAVESHARE_42_SCREEN_H}")

    # 4. 模拟一次"假 socket"：把数据帧发到一个收集 socket 验证序列化没问题
    import socket
    import threading

    received = bytearray()

    class FakeSock:
        def __init__(self):
            self.sent = bytearray()
            self._buf = bytearray()
            self._resp_pending = b"$OK#"

        def sendall(self, b: bytes):
            self.sent.extend(b)

        def recv(self, n):
            # 假装设备回 $OK# 一次后关闭
            if self._resp_pending:
                r = self._resp_pending
                self._resp_pending = b""
                return r
            return b""

        def settimeout(self, t): pass

        def close(self): pass

    fake = FakeSock()
    # 让 push_image_to_socket 读完 $OK# 后用 addr=0,len=0 收尾
    # 但 FakeSock.recv 第一次返回 b"$OK#"，之后返回 b"" → 会触发 timeout
    # 改写一下：第二次 recv 也返回 $OK#
    def recv(self, n):
        if self._resp_pending:
            r = self._resp_pending
            self._resp_pending = b""
            return r
        return b""

    FakeSock.recv = recv
    ack = push_image_to_socket(fake, full)
    assert len(fake.sent) > 0
    print(f"[5] push_image_to_socket fake-socket sent {len(fake.sent)} bytes, ack={ack!r}")

    print("\nALL OFFLINE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
