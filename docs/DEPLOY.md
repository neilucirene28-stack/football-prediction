# 部署说明（DEPLOY.md）

## 腾讯云（106.53.195.37，2026-10-13 前后到期）

**隔离原则**：v2 部署在 `/opt/football-v2`，与 v1 的 `/opt/football-prediction`
完全隔离；复用 v1 的 postgres 容器但新建 `football_v2` 库；绝不执行
`docker compose down -v`、不删除任何 volume、不覆盖 v1 的 `.env`。

### 一键部署（root，OrcaTerm 粘贴执行）

```bash
bash server_bootstrap.sh "<tarball下载链接>"
```

脚本幂等，可重复执行（只更新代码）。执行内容：
1. 下载解压代码到 `/opt/football-v2`
2. 建 venv、装依赖（`psycopg[binary]`、`fastapi`、`uvicorn`）
3. 只读 `/opt/football-prediction/.env` 取 postgres 密码，新建 `football_v2` 库并建表
4. demo 源冒烟测试（采集→落库）
5. 装 cron：每 6 小时 `xiaodianhuo` 采集（失败只记日志、不写 demo 数据），每天 03:30 复盘

### 日常检查

```bash
tail -f /var/log/football-v2/collect.log   # 采集日志
bash /opt/football-v2/scripts/cron_collect.sh   # 手动跑一次
```

### API 服务（可选，默认不启动）

```bash
cd /opt/football-v2/api
/opt/football-v2/venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8001
```

用 8001 端口，避免与 v1 的 3001 冲突。如需对外，加一条 nginx 反向代理即可。

## 采集数据流

```
cron(6h) → python -m collector --source xiaodianhuo --once
        → matches 表（upsert external_id+source）
        → odds_snapshots 表（opening/live/closing 三档快照）
        → 无 DB 时降级为 data/matches-*.json
```

`predict()` 的输入 `home_recent/away_recent/odds/opening_odds/h2h/injury`
即从 `matches.raw` + `odds_snapshots` 组装。

采集器为 Playwright 驱动真实页面（站内 API 请求加密，HTTP 直调不可用），
`server_bootstrap.sh` 会自动安装 Chromium（约 170MB）及系统依赖；
采集失败时 cron 只记错误日志并退出，绝不自动跑 demo 源（合成数据不得写入生产库）。

## 迁移到 Oracle Cloud（待服务器获批）

1. 新服务器上同样执行 `bash server_bootstrap.sh "<tarball链接>"`（脚本与云厂商无关）。
2. 数据迁移（二选一）：
   - **推荐**：`pg_dump -t matches -t odds_snapshots -t predictions football_v2`
     从腾讯云导出，在新库导入。历史采集数据无缝延续。
   - 或：新服务器从零开始采集（老数据留在腾讯云归档）。
3. 把 cron 停掉腾讯云那份（`crontab -e` 删除 football-v2 两行），避免两边重复采集。
4. GitHub：`v2` 分支即最新代码，两边部署都从同一分支拉取。

## 回滚

- 停采集：`crontab -e` 删除 football-v2 两行。
- 删 v2：`rm -rf /opt/football-v2`（v1 不受影响）。
