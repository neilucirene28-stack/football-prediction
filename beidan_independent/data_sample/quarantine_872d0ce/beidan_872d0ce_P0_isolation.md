# 872d0ce P0隔离报告（只读，不修改原文件）

commit: 872d0ce
报告生成: 2026-10-09T09:47Z
性质: 隔离记录，原导出文件未修改

## P0-1: 比托姆波罗尼亚是冰球（已确认隔离）

- 记录: provider=thesportsdb, provider_match_id=2591157
- team_cn=比托姆波罗尼亚, home=Polonia Bytom, away=GKS Tychy
- **league=Polish Hockey League** —— 冰球联赛，非足球
- 处置: **隔离，不得用于足球模型**
- 影响: 1条

## P0-2: 林茨蓝白误绑LASK（已确认误绑）

- 记录: provider=thesportsdb, provider_match_id=2611288
- team_cn=林茨蓝白, home=LASK, away=First Vienna
- thesportsdb_id=137261 指向 LASK (Linzer Athletik-Sport-Klub，奥甲)
- 北单seq20: 阿姆施泰滕 vs 林茨蓝白，奥乙——林茨蓝白=Blau-Weiß Linz（奥乙），与LASK是**不同俱乐部**
- 处置: **误绑，该条球队近况不可用**，需重新匹配Blau-Weiß Linz
- 影响: 1条

## P0-3: TSDB kickoff无时区（FIELD_DEFS错误）

- 29条thesportsdb记录的kickoff格式均为 `YYYY-MM-DD`（如2026-10-04）
- **无时区信息**，FIELD_DEFS声称"全部带时区"是错误的
- 处置: FIELD_DEFS需纠正为"TSDB仅日期，无时区"
- 影响: 29条

## P0-4: 269条含12条重复（去重清单）

- 269条对应257个唯一(provider, provider_match_id)
- 重复12条，全部来自ESPN:
  - 401877638 x2
  - 401873694 x2
  - 401873662 x2
  - 401859352 x2
  - 401859283 x2
  - （另2条见完整清单）
- 处置: 按(provider, provider_match_id)去重后为257条

## P0-5: raw文件非原始响应体

- data/exports/raw/espn_j1_20261009.json 等3个文件是加工后的列表
- 缺ESPN原始结构的 competitions/status/linescores，缺TSDB的 strSport/strTimestamp
- 处置: 本次交付真正的未改写原始响应体（见下）

## 最小可核验批次：J1四队ESPN原始响应体

| 球队 | ESPN ID | 文件 | 大小 | 收到UTC |
|---|---|---|---|---|
| 鹿岛鹿角 | 7115 | data/exports/raw/j1_true/espn_team_7115.json | 256,615 | 2026-10-09T09:46:04Z~09:46:07Z |
| 大阪钢巴 | 7102 | data/exports/raw/j1_true/espn_team_7102.json | 256,063 | 同上 |
| 柏太阳神 | 7476 | data/exports/raw/j1_true/espn_team_7476.json | 256,131 | 同上 |
| 神户胜利船 | 7477 | data/exports/raw/j1_true/espn_team_7477.json | 255,473 | 同上 |

- URL模板: `http://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/teams/{id}/schedule`（无key）
- 每队28场events，含主客方向(homeAway)、比分、winner标记
- 比赛结束状态: 有（status.type.completed / state=post/pre / description）
- **半场linescores: null**（schedule端点无此字段，需逐场summary端点，本次未取）
- 历史首次可用时间: 未知，本次采集仅供未来预测

## 纪律

- 未修改872d0ce任何原文件
- id_verified保持false，未声称人工核验
- 敏感扫描: 零key/凭证/服务器地址

