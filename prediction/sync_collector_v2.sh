#!/bin/bash
# sync_collector_v2.sh — 新版采集器轮次数据自动同步（由 cron 每小时调用）
# 仅处理 2026-09-24 11:00 之后已完成的轮次；按轮次及比赛幂等复跑。
set -u
BASE=/opt/football-prediction
DATA=/opt/football-collector-v2/football-collector-release/data
LOG=$BASE/logs/collector_v2_sync.log
LOCK=/tmp/collector_v2_sync.lock
mkdir -p "$BASE/logs"
exec 9>"$LOCK"
flock -n 9 || { echo "$(date -Is) another sync is running, skip" >> "$LOG"; exit 0; }
cd "$BASE"
set -a
. ./.env
set +a
echo "$(date -Is) sync start" >> "$LOG"
docker run --rm --network football-network \
  --env-file .env \
  -e DATABASE_HOST=football-postgres -e DATABASE_PORT=5432 \
  -e DATABASE_NAME=$POSTGRES_DB -e DATABASE_USER=$POSTGRES_USER -e DATABASE_PASSWORD=$POSTGRES_PASSWORD \
  -v $BASE/backend/app:/app/app:ro \
  -v $BASE/backend/tools_collector_v2_sync.py:/app/tools_collector_v2_sync.py:ro \
  -v $DATA:/app/collector_v2_data:ro \
  -v $BASE/data/raw:/app/data/raw \
  football-prediction-backend python tools_collector_v2_sync.py --after-run 2026-09-24-11-00-09 /app/collector_v2_data \
  >> "$LOG" 2>&1
echo "$(date -Is) sync exit=$?" >> "$LOG"
