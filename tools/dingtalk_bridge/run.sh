#!/usr/bin/env bash
# 一键重启钉钉桥:先杀掉在跑的旧进程 → 再前台启动新的(在本终端旁观每一步)。
# 用法:  bash tools/dingtalk_bridge/run.sh
set -e
cd "$(dirname "$0")/../.."          # 切到仓库根(无论从哪调用)
echo "→ 停掉在跑的旧桥(如果有)…"
pkill -f tools/dingtalk_bridge/bridge.py 2>/dev/null || true
sleep 1
echo "→ 启动新桥…"
exec python3 tools/dingtalk_bridge/bridge.py
