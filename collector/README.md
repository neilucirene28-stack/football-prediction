# 小店欢竞彩足球服务器采集器

本项目在 Ubuntu 服务器上使用 Node.js、Playwright 和 Chromium，从店铺分享入口进入竞彩足球列表，监听实际的 `SoccerMatchList`，按接口当前返回的比赛逐场打开详情并触发概况、阵容、战绩、欧指、亚指、排名、必发栏目；跳过情报。无需手机 Userscript 或人工逐场点击。

## 运行与验证状态（2026-09-26）

- 已在服务器实测店铺入口、比赛列表和逐场详情导航；不需要绕过登录或验证码。
- 13:00（Asia/Shanghai）轮次读取28场、处理28场、轮次错误0；7个栏目均逐场触发。该轮通过比赛卡片重新导航列表并连续处理所有比赛。
- 本轮有效数据：概况28、欧指28、亚指28、排名28、必发28；历史13。阵容接口返回非空名单12场，但非空名单不等于本场正式首发。
- `summary.json` 记录 `currentXIConfirmed` 与 `lineupStatus`。若来源没有明确确认标记，首发状态保留为“未确认”；原始阵容响应仍保存在比赛 JSON 中。
- `/qkdata/odds/list/<n>` 按响应 `playType` 分类；未知类型保存在 `unclassifiedOdds`。
- 未记录独立 `LIST_URL`；程序从 `ENTRY_URL` 打开店铺入口并等待实际 `SoccerMatchList` 响应，不猜测列表地址。

## 部署与运行

1. 将完整店铺分享入口保存在本地 `.env` 的 `ENTRY_URL` 中；不要上传或公开 `.env`。
2. 单场测试：`docker compose run --rm -e MAX_MATCHES=1 collector npm run once`。
3. 启动无人值守服务：`docker compose up -d`。`MAX_MATCHES=0` 表示处理当前列表全部符合条件的比赛。
4. 服务使用 `TZ=Asia/Shanghai`，每天10:00起每小时检查；开赛时间读取接口的 `match_time`，距开赛20分钟及以内会跳过。跨午夜场次按完整开赛时间处理。
5. 容器配置 `restart: unless-stopped`，数据、日志和浏览器 profile 保存在挂载目录。

## 数据位置和解释

- 每轮原始数据及 `summary.json`：`data/<轮次>/`。
- 运行日志：`logs/collector.log`。
- `missingModules` 表示没有有效模块响应；模块被点击不代表接口数据有效。
- 当前赛前名单接口可能返回非当前比赛阵容。应以 `currentXIConfirmed` 和 `lineupStatus` 判断是否有来源确认的本场首发；空值或未确认不得解释为正式首发。
- `headless=false` 只适用于配置了图形界面的环境。
