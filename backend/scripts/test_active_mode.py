"""主动模式：模拟"目标主机"（= 设备），让我们反连看设备在 AP 模式下行为。
注意：现在设备已配网，不会开 6868，所以这条路径是 dead end。
仅保留作为未来排查工具。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# 主要的探测已在 probe_waveshare_password.py 中实现
print("See probe_waveshare_password.py for the active probing logic")
