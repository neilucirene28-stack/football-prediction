# 新版采集器对接发布说明

## 变更内容
- backend/app/normalizer/adapters/collector_v2.py：新版采集器适配器（新增）
- backend/app/normalizer/normalizer.py：注册 collector_v2 来源
- backend/app/services/detail_modules.py：新版七模块保守展示（collector_v2_modules）
- backend/app/main.py：比赛详情接口附加 collector_v2 区块（旧接口结构不变）
- backend/tools_collector_v2_sync.py：轮次数据同步工具（新增）
- sync_collector_v2.sh：每小时自动同步脚本（新增，cron 5 10-23 * * *）

## 部署要点
1. 备份数据库与 backend/app 后，覆盖上述文件即可；网站容器无需重建。
2. 定时任务：5 10-23 * * * /opt/football-prediction/sync_collector_v2.sh
3. 同步幂等：按来源+内容哈希去重，重跑安全。
4. 回滚：恢复 backups/collector_v2_integration_20260923-2035/app 与数据库备份 db_before_collector_v2.sql.gz。
