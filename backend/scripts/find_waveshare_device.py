"""
扫描当前 LAN 内的微雪 4.2" e-Paper Cloud Module 设备。

原理：微雪 4.2" Cloud Module 默认在 AP 模式下为 192.168.4.1；
当设备 STA 模式连入你的路由器时，会从路由器 DHCP 池获得一个 IP。
本脚本会探测：
  1. 默认 AP IP 192.168.4.1
  2. 你当前 LAN 段（默认 192.168.1.0/24）里所有在线主机
  3. 对每个候选 IP 尝试 TCP 6868 握手 + 发出 ;ping/ 指令探测
"""
import argparse
import socket
import sys
import concurrent.futures
import ipaddress
import time


PORT = 6868
DEFAULT_AP_IP = "192.168.4.1"
PROBE_TIMEOUT = 0.6
CMD_PROBE = ";ping/".encode("ascii")  # 微雪协议指令模式探针


def detect_local_subnet() -> str:
    """通过 UDP socket 探出本机出口 IP，进而推断 /24 子网。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    # 默认按 /24 处理；如需更精细，可让用户传 --cidr
    parts = local_ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"


def ping_tcp(ip: str, port: int = PORT, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(CMD_PROBE)
            # 不严格等响应；只确认 socket 没被对方 RST
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def arp_alive_hosts(subnet_cidr: str, max_workers: int = 64) -> list[str]:
    """并发 TCP 探活，得到当前子网里 TCP 6868 端口开放的主机列表。"""
    net = ipaddress.ip_network(subnet_cidr, strict=False)
    candidates = [str(ip) for ip in net.hosts()]
    alive: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(ping_tcp, ip): ip for ip in candidates}
        for fut in concurrent.futures.as_completed(futs):
            ip = futs[fut]
            try:
                if fut.result():
                    alive.append(ip)
            except Exception:
                pass
    return sorted(alive, key=lambda x: tuple(int(p) for p in x.split(".")))


def probe_device(ip: str) -> dict:
    """主动发个 ;ping/，读响应帧，验证是不是微雪协议。"""
    info = {"ip": ip, "responds": False, "response": "", "elapsed_ms": 0}
    start = time.time()
    try:
        with socket.create_connection((ip, PORT), timeout=PROBE_TIMEOUT) as s:
            s.settimeout(PROBE_TIMEOUT)
            s.sendall(CMD_PROBE)
            try:
                data = s.recv(64)
                info["response"] = data.decode("ascii", errors="replace")
            except socket.timeout:
                pass
            info["responds"] = True
    except Exception as e:
        info["error"] = str(e)
    info["elapsed_ms"] = int((time.time() - start) * 1000)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描 LAN 内的微雪 4.2\" Cloud Module")
    ap.add_argument("--cidr", help="要扫描的子网，例如 192.168.1.0/24；不传则自动检测")
    ap.add_argument("--probe", action="store_true", help="对每个候选 IP 发 ;ping/ 协议探针")
    ap.add_argument("--ap", default=DEFAULT_AP_IP, help=f"微雪 AP 模式默认 IP（默认 {DEFAULT_AP_IP}）")
    args = ap.parse_args()

    print(f"[scan] 默认 AP 模式 IP: {args.ap}")
    print(f"[scan] 主动探测 TCP {PORT} ...")

    targets = []
    if args.ap:
        targets.append(args.ap)

    cidr = args.cidr or detect_local_subnet()
    print(f"[scan] 扫描子网: {cidr}")
    if args.cidr:
        # 用户明确指定了子网 → 直接在该子网里扫所有主机（不再依赖 ARP 表）
        net = ipaddress.ip_network(cidr, strict=False)
        targets.extend(str(ip) for ip in net.hosts())
    else:
        targets.extend(arp_alive_hosts(cidr))
    # 去重保序
    seen, ordered = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    print(f"[scan] 候选 {len(ordered)} 个 IP")
    for ip in ordered:
        if args.probe:
            info = probe_device(ip)
            tag = "OK " if info["responds"] else "-- "
            resp = info.get("response") or info.get("error", "")
            print(f"  {tag} {ip:>15s}  {info['elapsed_ms']:>4d}ms  {resp!r}")
        else:
            alive = ping_tcp(ip)
            tag = "OPEN " if alive else "---- "
            print(f"  {tag} {ip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
