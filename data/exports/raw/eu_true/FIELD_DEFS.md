# 欧洲10队ESPN原始schedule响应体 - 字段定义

采集时间：2026-10-09（真实start/end UTC见MANIFEST.json）
来源：ESPN隐藏API schedule端点（无key）

## 文件
- `espn_team_{id}.json`：每队一个未改写原始响应体
- `MANIFEST.json`：每请求的真实start/end UTC、HTTP状态、SHA256、大小
- `matching.jsonl`：北单(period,seq)与provider fixture匹配记录

## 10队
| 中文 | ESPN ID | 联赛 | 北单seq |
|---|---|---|---|
| 海登海姆 | 6418 | ger.2 | 18 |
| 凯泽斯劳滕 | 130 | ger.2 | 18 |
| 不伦瑞克 | 3067 | ger.2 | 19 |
| 基尔 | 7884 | ger.2 | 19 |
| 布兰 | 620 | nor.1 | 22 |
| 维京 | 510 | nor.1 | 22 |
| 北西兰 | 3101 | den.1 | 23 |
| 欧登塞 | 11550 | den.1 | 23 |
| 哥德堡 | 2556 | swe.1 | 24 |
| 韦斯特罗斯 | 22163 | swe.1 | 24 |

## 匹配状态
- 10/10 provider_fixture=null
- 原因：ESPN schedule端点仅返回已完赛场次，无未来fixture可匹配
- id_verified全部false（未人工核验）

## 同名球队隔离
- 哥德堡（2556，瑞典超）≠ 哥德堡盖斯（seq116）
- 基尔（7884，德乙）≠ 基尔马诺克（seq161，苏超）
