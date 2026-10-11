# BD-1 v16：Muse 数据核验与外部比分挖掘

截至北京时间2026年10月9日22:35，以v15提交 `4a4eadb3354d29e9257b77f04544e4fc1b59af14` 继续研究。用户提供的Muse清单已经用于定位原件，本次没有通过受地区限制的Meta AI网页发送问题，也没有将清单中的声明直接当成身份审批。

## 已接入的来源原件

原件固定在仓库提交 `373be6b66f916fa5940dd5a5de50cf21e56bb901`，按Git树列出的路径下载。249份文件全部与固定Git blob SHA和长度一致；其中243份实际请求体进一步与逐请求收据的完整SHA256、字节数对照。原请求收据时钟仍是采集方声明，本次独立结构核验使用本次真实时间。

| 数据 | 新增已核验FT/HT | 赛季／阶段 | 新增当期来源ID研究候选 |
|---|---:|---|---:|
| 英冠 eng.2 | 95 | 2026／14327 | 11 |
| 英乙 eng.4 | 102 | 2026／14339 | 12 |
| 合计 | 197 | 分联赛隔离 | 23 |

两批均无缺失summary、无拒收，球队schedule与summary的比赛ID、主客队ID、UTC开球、联赛、赛季阶段、90分钟FT比分和显式半场逐项一致。英冠的22队schedule并集不是已独立证明完整的联赛赛季，不能据此声称历史全覆盖。

原件保留在仓库原路径 `data/exports/raw/eng2_true/` 与 `data/exports/raw/eng4_true/`，不复制巨大响应体到独立目录。两个目录增加 `VERIFIED_FILE_INDEX.json`，其内容仅是收据索引。下载与Git摘要检查可用 `scripts/download_native_archives.py data_sample/native_archive_download_plan.json` 重现；在独立目录执行。

## 新的前瞻冻结

德乙54、英甲90、英冠95、英乙102，共341场来源历史。完整输入池179场不删减；40场来源ID研究候选、139场阻断，覆盖比例22.35%。这比v15的17场增加23场，但中文规范身份和北单fixture绑定批准数量仍为0。

冻结目录：`outputs/l1_prospective/20261009T143301921759Z/`。

- cutoff：`2026-10-09T14:32:44.360640+00:00`。
- 概率生成完成：`2026-10-09T14:33:01.904702+00:00`。
- 实际封存：`2026-10-09T14:33:02.958658+00:00`。
- 独立保留的清单SHA256：`fd34f7edc0df7d7adde4660cb42f1ce3baf7d659dc1b040460e9630a1f206b13`。
- 冷启动所选ridge仍为None；研究表的ridge=5不冒充已选模型。
- 空赛果原概率结算：40场pending、139场原冻结blocked、0场paired，Brier为null，production_gate_passed=false；结果见 `outputs/v16_pending_settlement.json`。

原v15冻结与其清单保持不变。同一比赛有多次赛前冻结，后续跨期汇总必须先固定所用版本，不能把它们重复计为独立样本。

## 自行挖掘的外部比分

从football-data.co.uk公开CSV获得2025/26赛季10个联赛的3336条FT/HT比分观察，均通过比分整数、胜平负一致性、半场不超过全场、赛季、未来日期与重复观察检查。字段说明原文：https://www.football-data.co.uk/notes.txt 。

| 来源代码 | 联赛 | FT/HT观察数 |
|---|---|---:|
| E3 | 英乙 | 552 |
| D1 | 德甲 | 306 |
| F2 | 法乙 | 305 |
| I2 | 意乙 | 380 |
| SP2 | 西乙 | 462 |
| SC0 | 苏超 | 228 |
| SC1 | 苏冠 | 180 |
| N1 | 荷甲 | 306 |
| P1 | 葡超 | 306 |
| B1 | 比甲 | 311 |
| 合计 | 10个联赛 | 3336 |

保存目录 `data_sample/football_data_csv/extracted_20261009/` 含原提取文本、规范化文本、RETRIEVAL.json、AUDIT.json和score_observations.jsonl。`scripts/audit_extracted_csv.py`可重现规范化和审计。

直接HTTP下载被当前网络策略阻断，因此改用Tavily提取公开CSV文本。原HTTP响应字节没有取得；extracted_text_sha256仅标识连接器文本，normalized_text_sha256仅标识规范化文本，绝不当作提供商原HTTP-body摘要。部分提取文本丢失换行，只在没有引号、具有明确“联赛代码,日期,”行标记时恢复边界，原提取内容不覆盖。核验时刻为 `2026-10-09T14:34:08.629468+00:00`，不能倒填到2025/26赛前。

这些CSV只有球队名称，没有可用于跨源绑定的球队ID，也没有已核实的开球时区和历史发布时刻。因此新增L1导入数为0；赔率、亚洲让球、开奖SP均未导入模型。来源观察数不代表已独立核实完整联赛赛季，尤其法乙305和比甲311不能凑数补成预期总量。E1英冠公开CSV本次提取失败，明确记录在账本；当前英冠研究依靠上面的ESPN95场。

## 清单中的其他资料与剩余缺口

J1的30份summary仍保留。其4队是鹿岛鹿角、大阪钢巴、柏太阳神、神户胜利船；当期来源scoreboard的4场是另外8队，不能因“同属J1”就增加当期球队覆盖。API-Football1450场fixtures不替代经过核验的球队历史或中文身份审批；免费版2026赛季查询失败不绕过。1665场北单旧赛果仍只能用于比分分布研究，不能做历史as-of实战验证。

旧账本“5场严格匹配、174场未匹配”和新冻结“40场来源ID研究候选、139场阻断”是不同口径；前者没有被改写成40场已批准匹配。176场整数线/SP的归档核验也不自动等于当前冻结具备新鲜官方采集证据，冻结中的official_handicap仍为null。

当前仍缺139场可用的来源球队历史/fixture证据、中文规范身份绑定审批、新鲜官方北单整数让球/SP证据，以及至少5期/500场独立成对赛果的Brier、比分log loss和进球均值验证。不得上线或宣称准确率改善。

## 北京时间今日与明日预测

用户随后询问当天预测。已核对固定提交中的193场当前池：北京时间10月9日有8场足球；本次运行时6场已开球，保留跳过账本。胡内多阿拉vs沃伦塔里、塞伊奈vs拉赫蒂两场23:00赛事只生成真实赛前的L3根先验观察快照，规范球队ID与赛事族未绑定、让球为空、observation_only=true，不能冒充球队实力预测。日历子池的范围没有被当作完整官方开售池。

`outputs/predictions_20261009_20261010.md`分开列出今天8场状态与明天40场来源ID研究候选；明确区分已选L3冷启动概率和L1 ridge=5诊断概率，读原冻结不重新拟合。实际运行账本与两场观察快照位于 `outputs/today_calendar_shadow/`，另有 `outputs/today_calendar_status.json`。六场早开球比赛没有补造赛前预测。

## 验证与复现

110项标准库unittest通过，新增6项CSV边界与数据隔离测试。两批原件243个载荷完整SHA256检查通过；249个固定Git blob检查通过；新冻结通过整包封存校验和空赛果原概率计分。Git提交增量清单见 `V16_DELTA_MANIFEST.json`。

```sh
PYTHONPATH=. python -m unittest discover -s tests
PYTHONPATH=. python scripts/audit_native_batch.py data_sample/espn_eng2/AUDIT_CONFIG.json
PYTHONPATH=. python scripts/audit_native_batch.py data_sample/espn_eng4/AUDIT_CONFIG.json
PYTHONPATH=. python scripts/audit_extracted_csv.py
```

重新运行审计会记录新的真实核验时间；审计输出可重建，但不能用新时间改写已有冻结包。赛后继续使用v15结算器对选定的原冻结概率直接计分。
