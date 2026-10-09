# GitHub 项目更新盯盘清单

用户规矩（2026-10-08）：每周检查一次这些项目的作者有没有发新版本/新功能，有就更新。

## 在盯列表

| 仓库 | 用途/关系 | 状态 |
|---|---|---|
| `omkarcloud/flashscore-scraper` | 数据源调研候选 | 待确认是否采用 |
| `omkarcloud/sofascore-scraper` | 数据源调研候选 | 待确认是否采用 |
| `vasthornet/espn-sports-scraper` | 数据源调研候选（ESPN） | 待确认是否采用 |
| `withqwerty/open-football` | 数据源调研候选 | 待确认是否采用 |
| `openfootball` org | football-data.co.uk 上游数据 | 在用（数据源） |

## 检查方法
每周一跑 `scripts/check_github_updates.py`（或 cron 内联）：
- GitHub API：`GET /repos/{owner}/{repo}/releases/latest` 看新版本
- `GET /repos/{owner}/{repo}/commits?since=<7天前>` 看新提交
- 有更新 → 汇总 changelog，要点汇报用户；咱们在用的项目评估是否升级

## 变更记录
- 2026-10-08：建清单，初始5个（来自 datasource-research.md 调研）
