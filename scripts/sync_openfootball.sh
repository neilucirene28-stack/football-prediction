#!/usr/bin/env bash
# openfootball 数据仓库定期同步 (git pull --ff-only, 只读更新)
# 用法: scripts/sync_openfootball.sh
# cron 建议: 每周一 07:30 跑一次 (上游按赛季更新, 非实时源)
#   30 7 * * 1  cd ~/workspace/football-prediction-v2 && scripts/sync_openfootball.sh >> data/openfootball_sync.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/openfootball_sync.log"

{
  echo "=== $(date '+%F %T') openfootball sync start ==="
  for repo in football.json football.db; do
    d="$ROOT/data/$repo"
    if [ -d "$d/.git" ]; then
      before=$(git -C "$d" rev-parse --short HEAD)
      if git -C "$d" pull --ff-only 2>&1 | tail -2; then
        after=$(git -C "$d" rev-parse --short HEAD)
        if [ "$before" = "$after" ]; then
          echo "[$repo] 已是最新 ($after)"
        else
          echo "[$repo] 更新 $before -> $after"
        fi
      else
        echo "[$repo] pull 失败, 保持 $before"
      fi
    else
      echo "[$repo] 目录不存在, 跳过"
    fi
  done
  echo "=== $(date '+%F %T') openfootball sync done ==="
} | tee -a "$LOG"
