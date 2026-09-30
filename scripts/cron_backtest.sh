#!/usr/bin/env bash
# 每日复盘：汇总 backtest_results（有 API 服务时调接口，无服务则跳过）
set -uo pipefail
INSTALL_DIR="/opt/football-v2"
echo "[$(date '+%F %T')] backtest tick"
# API 若在跑则触发复盘汇总；没跑也不报错
curl -s --max-time 10 http://127.0.0.1:8000/api/backtest/summary 2>/dev/null \
  | head -c 500 || echo "api not running, skip"
echo ""
