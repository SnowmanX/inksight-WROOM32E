#!/usr/bin/env bash
# 停止 start.sh 拉起的所有 InkSight + Waveshare 服务
# 兼容 macOS / Linux
set +e

echo "停止 InkSight Backend..."
pkill -f "uvicorn api.index:app" 2>/dev/null
echo "停止 Waveshare Bridge..."
pkill -f "waveshare_bridge" 2>/dev/null
echo "停止 Webapp (next dev)..."
pkill -f "next dev" 2>/dev/null

sleep 1
echo "已停止。"
