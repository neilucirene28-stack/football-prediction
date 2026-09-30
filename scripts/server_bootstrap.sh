#!/usr/bin/env bash
# football-prediction-v2 一键部署（腾讯云 / 以后 Oracle 通用）
# 用法（root）：bash server_bootstrap.sh "<tarball下载链接>"
#
# 做什么：
#  1. 把代码解压到 /opt/football-v2（与 /opt/football-prediction 完全隔离）
#  2. 建 Python venv 并装依赖
#  3. 在已有的 postgres 容器里新建 football_v2 库并建表（只读 v1 的 .env 取密码，不改 v1 任何东西）
#  4. 用 demo 源冒烟测试采集→本地文件（不写生产库）
#  5. 装 cron：每 6 小时采集一次（xiaodianhuo），每天凌晨跑一次复盘汇总
# 可重复执行：重复跑只会更新代码，不会重建库、不会动 v1。
set -euo pipefail

TARBALL_URL="${1:?用法: bash server_bootstrap.sh \"<tarball链接>\"}"
INSTALL_DIR="/opt/football-v2"
VENV="$INSTALL_DIR/venv"
LOG_DIR="/var/log/football-v2"
LOG="$LOG_DIR/bootstrap.log"
V1_ENV="/opt/football-prediction/.env"

echo "==> 1/5 准备目录 $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$LOG_DIR"
cd /tmp
rm -f v2.tar.gz
curl -sSL --max-time 120 -o v2.tar.gz "$TARBALL_URL"
tar xzf v2.tar.gz -C "$INSTALL_DIR" --strip-components=1
echo "    代码已更新: $(ls $INSTALL_DIR | tr '\n' ' ')"

echo "==> 2/5 安装 Python 依赖"
if ! python3 -c "import venv" 2>/dev/null; then
  echo "    安装 python3-venv..."
  apt-get update -qq && apt-get install -y -qq python3-venv
fi
if [ ! -d "$VENV" ]; then
  if ! python3 -m venv "$VENV" 2>/dev/null; then
    echo "    系统 ensurepip 缺失，改用 --without-pip + get-pip.py ..."
    rm -rf "$VENV"
    python3 -m venv --without-pip "$VENV"
    curl -sSL --max-time 60 -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py -q
  fi
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$INSTALL_DIR/collector/requirements.txt" \
    -r "$INSTALL_DIR/api/requirements.txt"
echo "    安装 Playwright Chromium..."
"$VENV/bin/python" -m playwright install --with-deps chromium >>"$LOG" 2>&1 \
  || "$VENV/bin/python" -m playwright install chromium >>"$LOG" 2>&1
echo "    依赖就绪"

echo "==> 3/5 数据库（复用现有 postgres 容器，新建 football_v2 库）"
if [ ! -f "$V1_ENV" ]; then echo "    !! 未找到 $V1_ENV，跳过建库（采集将降级为 JSON 文件）"; else
  # 只读 v1 的 .env，绝不修改
  PGUSER="$(grep -E '^POSTGRES_USER=' "$V1_ENV" | cut -d= -f2 | sed 's/["\r]//g')"
  PGPASS="$(grep -E '^POSTGRES_PASSWORD=' "$V1_ENV" | cut -d= -f2 | sed 's/["\r]//g')"
  PGDB="$(grep -E '^POSTGRES_DB=' "$V1_ENV" | cut -d= -f2 | sed 's/["\r]//g')"
  PGDB="${PGDB:-postgres}"
  PGCONT="$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)"
  if [ -z "${PGCONT:-}" ]; then echo "    !! 未找到运行中的 postgres 容器，跳过"; else
    echo "    使用容器 $PGCONT，用户 $PGUSER"
    if ! docker exec "$PGCONT" psql -U "$PGUSER" -d "$PGDB" -tAc \
        "SELECT 1 FROM pg_database WHERE datname='football_v2'" | grep -q 1; then
      docker exec -e PGPASSWORD="$PGPASS" "$PGCONT" \
        psql -U "$PGUSER" -d "$PGDB" -c "CREATE DATABASE football_v2" >/dev/null
      echo "    已创建数据库 football_v2"
    else
      echo "    football_v2 已存在，跳过创建"
    fi
    docker exec -i -e PGPASSWORD="$PGPASS" "$PGCONT" psql -U "$PGUSER" \
      -d football_v2 -v ON_ERROR_STOP=1 \
      -f - < "$INSTALL_DIR/sql/001_init.sql" >/dev/null
    echo "    表结构已同步"
    export DATABASE_URL="postgresql://$PGUSER:$PGPASS@localhost:5432/football_v2"
    # 落盘一份给 cron 用（仅 root 可读）
    printf '%s\n' "$DATABASE_URL" > "$INSTALL_DIR/.database_url"
    chmod 600 "$INSTALL_DIR/.database_url"
  fi
fi

echo "==> 4/5 冒烟测试（demo 源采集→本地文件，绝不写生产库）"
cd "$INSTALL_DIR/collector"
unset DATABASE_URL  # 关键：冒烟测试不许碰 football_v2 生产库
PYTHONPATH="$INSTALL_DIR" "$VENV/bin/python" -m collector --source demo --once 2>&1 | tail -3
echo "    引擎内存冒烟："
PYTHONPATH="$INSTALL_DIR" "$VENV/bin/python" - <<'EOF' 2>&1 | tail -2
from datetime import datetime, timedelta, timezone
from engine.predictor import predict
now = datetime.now(timezone.utc)
r = predict({"home": "DemoA", "away": "DemoB",
             "kickoff_at": (now + timedelta(days=2)).isoformat(),
             "snapshot_at": now.isoformat()})
print("predict ok, keys:", sorted(r.keys())[:5])
EOF

echo "==> 5/5 安装 cron 定时任务"
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v "football-v2" > "$CRON_TMP" || true
cat >> "$CRON_TMP" <<EOF
# football-v2 采集与复盘（/opt/football-v2 独立运行，不碰 v1）
0 */6 * * * /usr/bin/flock -n /tmp/football-v2-collect.lock $INSTALL_DIR/scripts/cron_collect.sh >> $LOG_DIR/collect.log 2>&1
30 3 * * * /usr/bin/flock -n /tmp/football-v2-backtest.lock $INSTALL_DIR/scripts/cron_backtest.sh >> $LOG_DIR/backtest.log 2>&1
EOF
crontab "$CRON_TMP"; rm -f "$CRON_TMP"
echo "    cron 已安装：每6小时采集 / 每天03:30复盘"

echo ""
echo "完成。检查：tail -f $LOG_DIR/collect.log ；手动跑一次：bash $INSTALL_DIR/scripts/cron_collect.sh"
