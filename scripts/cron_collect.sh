#!/usr/bin/env bash
# 定时采集：小店火真实源。
# 真实采集失败时只记录错误并退出，绝不自动跑 demo 源——
# 合成数据一旦写入生产库就无法区分，会污染回测，这是数据纪律红线。
set -uo pipefail
INSTALL_DIR="/opt/football-v2"
LOG_DIR="/var/log/football-v2"
cd "$INSTALL_DIR/collector"
if [ -f "$INSTALL_DIR/.database_url" ]; then
  export DATABASE_URL="$(cat "$INSTALL_DIR/.database_url")"
fi
export PYTHONPATH="$INSTALL_DIR"
export SOURCE="${SOURCE:-xiaodianhuo}"
if ! "$INSTALL_DIR/venv/bin/python" -m collector --source "$SOURCE" --once; then
  echo "[$(date '+%F %T')] 采集失败（source=$SOURCE），已跳过写库" \
    >> "$LOG_DIR/collect-error.log"
  exit 1
fi
