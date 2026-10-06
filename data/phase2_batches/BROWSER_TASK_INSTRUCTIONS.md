# Phase 2 回填：澳客历史战绩抓取（单批指令模板）

你是负责抓取公开足球数据的 browser task。**只读公开页面，不登录、不点击任何投注/广告链接。**

## 输入
本批比赛清单见随附的 batch_XX.json 文件（约 19 场）。每场包含：
- `mido`：澳客比赛 ID
- `league` / `home` / `away`：联赛、主队、客队（中文名）
- `kickoff`：开球时间，如 "2026-09-02 02:00"
- `asof`：as-of 截断日期（YYYY-MM-DD），等于 kickoff 的日期部分

## 对每场比赛执行
1. 访问 `https://www.okooo.com/soccer/match/{mido}/history/`（把 {mido} 换成实际 ID）
2. 如果页面打不开/404/被拦截，该场记 `"error": "页面无法访问"`，继续下一场，**不要重试超过 2 次**
3. 提取以下数据：

### a) 本场完赛比分（页面顶部）
- 如显示 "3-0（半场2-0）" → `hg: 3, ag: 0, ht_hg: 2, ht_ag: 0`
- 如未完赛/无比分 → `hg: null, ag: null, ht_hg: null, ht_ag: null`

### b) 主队近10场战绩（页面"主队近况/历史战绩"区域）
每场记录：`{date, league, home, away, hg, ag, venue}`
- `date`：比赛日期，统一为 YYYY-MM-DD
- `league`：联赛名（页面显示什么写什么）
- `home` / `away`：对阵双方（主队在前）
- `hg` / `ag`：主队进球 / 客队进球（整数）
- `venue`：该队在这场比赛是 `"home"` 还是 `"away"`

### c) 客队近10场战绩
同上格式，venue 指客队是主/客场。

## 铁律：as-of 截断（绝不能违反）
- `home_form` / `away_form` 里**每一场**的 `date` 必须**严格早于**该场的 `asof` 日期
- 页面可能列出 asof 之后（含当天晚于开球时间）的比赛，**必须排除**
- 如某队 as-of 之前不足 10 场，有几场写几场，**不许用 as-of 之后的比赛凑数**
- 日期存疑（页面没写年份等）时宁可舍弃该场，记 `"error": "日期不明已舍弃"`

## 输出
将结果写入 `/home/hatch/workspace/football-prediction-v2/data/phase2_batches/result_XX.json`（XX 为本批编号），格式：
```json
[
  {"mido": "1345995", "hg": 3, "ag": 0, "ht_hg": 2, "ht_ag": 0,
   "home_form": [{"date": "2026-08-25", "league": "沙特联", "home": "利雅新月", "away": "吉达国民", "hg": 2, "ag": 1, "venue": "home"}],
   "away_form": [...]},
  {"mido": "1345996", "error": "页面无法访问"},
  ...
]
```
- 每个 mido 必须出现一次：成功则给完整数据，失败则给 `{"mido": "...", "error": "原因"}`
- 不要输出除 JSON 文件之外的多余解释
