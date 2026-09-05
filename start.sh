#!/usr/bin/env bash
# InkSight + Waveshare 一键启动 (macOS / Linux)
#
# 同时拉起 3 个进程 (后台):
#   1) InkSight Backend (FastAPI, 端口 8080)
#   2) Waveshare Bridge   (FastAPI + TCP 6868, 端口 9000)
#   3) Webapp             (Next.js,   端口 3000)
#
# 使用方法:
#   1) 把下面 DEVICE_HOST 改成你电脑的局域网 IP (ifconfig/ip addr 查看)
#   2) bash start.sh
#   3) 浏览器打开 http://127.0.0.1:3000/cloud-module
#
# 关闭: bash stop.sh (或手动 kill, 见下方 PID 输出)

set -e

# === 你需要改的唯一配置: 你电脑的局域网 IP ===
DEVICE_HOST="${DEVICE_HOST:-192.168.1.195}"
BRIDGE_HTTP_PORT="${BRIDGE_HTTP_PORT:-9000}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
WEBAPP_PORT="${WEBAPP_PORT:-3000}"

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
WEBAPP_DIR="$REPO_ROOT/webapp"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

# 找 venv
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON_EXE="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON_EXE="python3"
fi

echo "============================================"
echo " InkSight + Waveshare Bridge 启动器"
echo " 后端:   http://127.0.0.1:$BACKEND_PORT"
echo " Bridge: http://127.0.0.1:$BRIDGE_HTTP_PORT  + TCP 6868"
echo " Web:    http://127.0.0.1:$WEBAPP_PORT"
echo " 目标主机: $DEVICE_HOST"
echo "============================================"
echo

# 清理可能残留的旧进程
pkill -f "uvicorn api.index:app" 2>/dev/null || true
pkill -f "waveshare_bridge" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 1

# === 1) InkSight Backend ===
echo "[1/3] 启动 InkSight Backend (端口 $BACKEND_PORT)..."
(cd "$BACKEND_DIR" && nohup $PYTHON_EXE -m uvicorn api.index:app --host 0.0.0.0 --port $BACKEND_PORT > "$LOG_DIR/backend.log" 2>&1 &)
echo "      PID=$!  日志: $LOG_DIR/backend.log"
echo $! > "$LOG_DIR/backend.pid"

# === 2) Waveshare Bridge ===
echo "[2/3] 启动 Waveshare Bridge (HTTP $BRIDGE_HTTP_PORT + TCP 6868)..."
(cd "$BACKEND_DIR" && BRIDGE_LOG_FILE="$LOG_DIR/bridge.log" nohup $PYTHON_EXE -m backend.scripts.waveshare_bridge --device-ip $DEVICE_HOST --port $BRIDGE_HTTP_PORT > "$LOG_DIR/bridge.stdout.log" 2>&1 &)
echo "      日志: $LOG_DIR/bridge.log"
BRIDGE_PID=$(pgrep -f "waveshare_bridge" | tail -n 1)
echo "      PID=$BRIDGE_PID"

# === 3) Webapp ===
echo "[3/3] 启动 Webapp (端口 $WEBAPP_PORT)..."
(cd "$WEBAPP_DIR" && nohup npx next dev -p $WEBAPP_PORT > "$LOG_DIR/webapp.log" 2>&1 &)
echo "      日志: $LOG_DIR/webapp.log"
WEBAPP_PID=$(pgrep -f "next dev" | tail -n 1)
echo "      PID=$WEBAPP_PID"

echo
echo "三个服务已启动, 等待 10 秒加载..."
echo "然后浏览器打开: http://127.0.0.1:$WEBAPP_PORT/cloud-module"
echo
echo "停止所有服务: bash stop.sh"

# 等 10 秒让 next dev 起来, 然后用默认浏览器打开 webapp
sleep 10
URL="http://127.0.0.1:$WEBAPP_PORT/cloud-module"

case "$(uname -s)" in
  Darwin)  open "$URL" ;;
  Linux)   xdg-open "$URL" >/dev/null 2>&1 || echo "(未找到 xdg-open, 请手动打开 $URL)" ;;
  *)       echo "(未知系统, 请手动打开 $URL)" ;;
esac
