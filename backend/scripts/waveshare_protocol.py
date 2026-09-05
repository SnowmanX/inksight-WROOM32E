"""
Waveshare 4.2" e-Paper Cloud Module TCP 协议封装（按 headblockhead 官方逆向文档）。

帧分两种模式：

  1) 指令模式
     格式: ';' + cmd (+ 可选数据) + '/' + 校验
     例:   ';C/'                ← 检查设备是否锁定（不需要 unlock）
           ';N<password>/'      ← 解锁（不需要 unlock）
           ';S/'                ← 关机（需要 unlock）
     响应: '$' + data + '#'

  2) 数据模式（图像数据下发）
     格式: 0x57 + 4B addr (BE) + 4B len (BE) + 1B num + len B data + 校验
     校验: XOR over (0x57, addr 4B, len 4B, num, data bytes)
     建议: 数据帧 ≤ 1100 字节，num 固定为 0x00（软件 bug）
     收尾: addr=0 且 len=0 → 设备自动触发刷新

返回帧统一格式: '$' + data + '#'

⚠️ 重要：除 `C`、`N`、`G`、以外的所有指令（包括下发数据、刷新、关机）都需要设备
 处于"已解锁"状态。未解锁的设备会忽略所有受限指令。
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass


CMD_HEAD = b";"
CMD_TAIL = b"/"
DATA_HEAD = 0x57
RESP_HEAD = b"$"
RESP_TAIL = b"#"

DEFAULT_PORT = 6868
DEFAULT_TIMEOUT = 5.0

DEFAULT_PASSWORD = "0000"  # 设备默认未修改的密码（headblockhead 文档惯例）


class WaveshareProtocolError(RuntimeError):
    pass


def xor_checksum(buf: bytes) -> int:
    cs = 0
    for b in buf:
        cs ^= b
    return cs & 0xFF


def build_cmd(cmd: str, payload: bytes = b"") -> bytes:
    """构造指令模式帧: ';' + cmd (+ payload) + '/' + 校验

    headblockhead 文档 Go 示例：
        command := "C"
        var check uint32
        for i := 0; i < len(command); i++ {
            check = check ^ uint32(command[i])
        }
        display.Connection.Write([]byte(";" + command + "/" + string(rune(check))))

    关键: checksum 只 XOR cmd 字符（不含 ; / payload）。
    payload 不参与 checksum（文档示例没显示 payload，但同协议）。
    一些研究: headblockhead 实际 N 解锁用法: cs = XOR('N'+password)。
    所以 checksum = XOR(cmd + payload)。
    """
    body = cmd.encode("ascii")
    # 关键: checksum 只 XOR cmd + payload (不含 ';' 头和 '/' 分隔符)
    cs = 0
    for b in body:
        cs ^= b
    for b in payload:
        cs ^= b
    cs &= 0xFF
    frame_no_cs = CMD_HEAD + body + payload + CMD_TAIL
    return frame_no_cs + bytes([cs])


def build_data(addr: int, data: bytes, num: int = 0) -> bytes:
    """构造数据模式帧: 0x57 + 4B addr + 4B len(实际) + 1B num + data + 1B cs

    关键发现 (v31): 官方 Cloud_WIN 的数据帧长度不是固定的 1024!
    看 4.2inch_display_EPD.py / tcp_sver.flush_buffer:
        for i in range(0, math.ceil(self.size/self.lenght)):
            leng = self.lenght       # 可能是 1024 也可能是最后一段
            addr = i*leng
            data = struct.pack(">IIB", addr, leng, num)
            ...
            for j in range(0, leng):
                if (i*leng+j)<len(DATA):
                    data = data+[DATA[j+i*leng]]
                else:
                    data = data+[0xFF]
            self.Send_data(data)
    data=[0,0,0,0,0,0,0,0,0,0,0]
    self.Send_data(data)   # EOT 11 字节 0x00

    关键修复:
    1) 每帧 len 字段 = 实际数据长度 (不是固定 1024)
    2) 最后一帧只发剩余字节数 (15000-14336=664 字节), 用 len=664
    3) 帧数 = ceil(15000/1024) = 15 帧
       帧 0:  addr=0,     len=1024,  字节 0-1023
       帧 1:  addr=1024,  len=1024,  字节 1024-2047
       ...
       帧 14: addr=14336, len=664,   字节 14336-14999
    4) checksum = XOR(原始 data bytes, 不含 0x57 头)
    """
    if not 0 <= addr <= 0xFFFFFFFF:
        raise ValueError("addr out of range")
    if len(data) > 1024:
        raise ValueError("data length exceeds 1024")

    # 不补 0xFF, 用真实长度
    data_actual = data
    length = len(data_actual)

    body = struct.pack(">I", addr) + struct.pack(">I", length) + bytes([num & 0xFF]) + data_actual
    cs = 0
    for b in body:
        cs ^= b
    cs &= 0xFF
    return bytes([DATA_HEAD]) + body + bytes([cs])


def build_close_frame() -> bytes:
    """EOT 收尾帧: 0x57 + 11字节 0x00 + cs(0x00)

    官方 Cloud_WIN:
        data=[0,0,0,0,0,0,0,0,0,0,0]   # 11 字节全 0
        self.Send_data(data)
    Send_data: cs = XOR(11 字节 0) = 0x00
    """
    body = b"\x00" * 11
    cs = 0
    return bytes([DATA_HEAD]) + body + bytes([cs])


def read_response(sock: socket.socket, timeout: float = 2.0) -> str:
    """读响应：尽可能读完 5~10 字节响应。

    实测：设备的 ;C/ 和 ;N<pw>/ 响应是 $cmd#\\x00\\x00 (5 字节)
    TCP 是字节流，recv(4096) 一次可能拿到"两个 ack 粘在一起"，
    所以我们 read_exactly(5) 精确读 5 字节。
    """
    sock.settimeout(timeout)
    try:
        return _read_exact(sock, 5).decode("ascii", errors="replace")
    except socket.timeout:
        return ""
    except Exception as e:
        return f"<err:{e}>"


def read_response_2step(sock: socket.socket, timeout: float = 2.0) -> tuple[str, str]:
    """读 2 步响应 (1st=Parity Bit, 2nd=Result)。

    headblockhead 文档: ;C/ 回 parity + locked?, ;N<pw>/ 回 parity + success?
    设备实际可能合并 2 个响应为 10 字节, 也可能分开发, 也可能只发 1 个.
    所以我们 read_exactly(10) 一次性读 10 字节, 拆成 2 个 5 字节响应.
    """
    sock.settimeout(timeout)
    try:
        buf = _read_exact(sock, 10)
        a = buf[0:5].decode("ascii", errors="replace")
        b = buf[5:10].decode("ascii", errors="replace")
        return a, b
    except socket.timeout:
        # 读 5 字节 (Parity) 后 timeout
        try:
            a = _read_exact(sock, 5).decode("ascii", errors="replace")
            return a, ""
        except Exception:
            return "", ""
    except Exception as e:
        return "", f"<err:{e}>"


def _read_exact(sock: socket.socket, n: int) -> bytes:
    """精确读 n 字节。"""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def send_and_read(sock: socket.socket, frame: bytes, timeout: float = 2.0) -> str:
    sock.sendall(frame)
    return read_response(sock, timeout=timeout)


@dataclass
class WaveshareDevice:
    host: str
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    password: str = DEFAULT_PASSWORD
    # 设备是否"已解锁"——长连接中保持状态
    _unlocked: bool = False

    def _connect(self) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def ping(self) -> str:
        with self._connect() as s:
            return send_and_read(s, build_cmd("ping"), timeout=1.5)

    def check_locked(self) -> str:
        """发送 ';C/' → 第 1 响应 = Parity Bit，第 2 响应 = '0' (unlocked) / '1' (locked)"""
        with self._connect() as s:
            send_and_read(s, build_cmd("C"), timeout=1.5)
            locked = read_response(s, timeout=1.5)
            return locked

    def unlock(self, password: str = "") -> str:
        """发送 ';N<password>/' → 设备解锁。所有受限指令（push/refresh/shutdown）依赖此状态。"""
        pw = password or self.password
        with self._connect() as s:
            ack1 = send_and_read(s, build_cmd("N", pw.encode("ascii")), timeout=2.0)
            ack2 = read_response(s, timeout=2.0)
            if ack2.strip() in ("successful", "success", "ok", "0", ""):
                self._unlocked = True
            return f"{ack1!r} | {ack2!r}"

    def unlock_session(self) -> None:
        """每次新连接前都要先 unlock。"""
        with self._connect() as s:
            send_and_read(s, build_cmd("C"), timeout=1.5)
            read_response(s, timeout=1.5)  # locked?
            send_and_read(s, build_cmd("N", self.password.encode("ascii")), timeout=2.0)
            ack2 = read_response(s, timeout=2.0)
            if ack2.strip() in ("successful", "success", "ok", "0"):
                self._unlocked = True
            else:
                raise WaveshareProtocolError(f"unlock failed: {ack2!r}")

    def send_data_frame(self, addr: int, data_chunk: bytes, num: int = 0) -> str:
        """下发一段数据帧。响应: parity + success (两步)"""
        with self._connect() as s:
            if not self._unlocked:
                self.unlock_session()
            ack1 = send_and_read(s, build_data(addr, data_chunk, num=num), timeout=3.0)
            ack2 = read_response(s, timeout=3.0)
            return f"{ack1!r} | {ack2!r}"

    def send_eot(self) -> str:
        """发送 end-of-transmission 帧（addr=0, len=0）→ 触发设备刷新"""
        with self._connect() as s:
            if not self._unlocked:
                self.unlock_session()
            ack1 = send_and_read(s, build_data(addr=0, data=b"", num=0), timeout=3.0)
            ack2 = read_response(s, timeout=8.0)  # 墨水屏 4s
            return f"{ack1!r} | {ack2!r}"

    def push_image_and_refresh(self, image_bytes: bytes, *, frame_size: int = 1024) -> str:
        """一站式：unlock → 拆帧推图 → EOT 触发刷新。
        每帧 ≤ 1100 字节 (headblockhead 推荐)，我们用 1024。
        """
        if len(image_bytes) != WAVESHARE_42_FRAME_BYTES:
            raise ValueError(
                f"image_bytes must be exactly {WAVESHARE_42_FRAME_BYTES} bytes (got {len(image_bytes)})"
            )

        s = self._connect()
        try:
            # 1) 握手：检查锁定状态
            send_and_read(s, build_cmd("C"), timeout=1.5)
            read_response(s, timeout=1.5)

            # 2) 解锁
            send_and_read(s, build_cmd("N", self.password.encode("ascii")), timeout=2.0)
            ack2 = read_response(s, timeout=2.0)
            self._unlocked = ack2.strip() in ("successful", "success", "ok", "0")

            # 3) 拆帧推图
            addr = 0
            for idx, chunk in enumerate(
                (image_bytes[i : i + frame_size] for i in range(0, len(image_bytes), frame_size)),
                start=1,
            ):
                send_and_read(s, build_data(addr=addr, data=chunk, num=0), timeout=3.0)
                read_response(s, timeout=3.0)  # success
                addr += len(chunk)

            # 4) 收尾帧 (addr=0, len=0) → 设备自动刷新
            send_and_read(s, build_data(addr=0, data=b"", num=0), timeout=3.0)
            tail = read_response(s, timeout=8.0)
            return f"unlocked={self._unlocked} frames={addr // frame_size} tail={tail!r}"
        finally:
            self._unlocked = False  # 长连接断开后必须重新 unlock
            try:
                s.close()
            except OSError:
                pass


# 1bpp 400x300 屏幕常量
WAVESHARE_42_SCREEN_W = 400
WAVESHARE_42_SCREEN_H = 300
WAVESHARE_42_FRAME_BYTES = (WAVESHARE_42_SCREEN_W * WAVESHARE_42_SCREEN_H) // 8  # 15000
