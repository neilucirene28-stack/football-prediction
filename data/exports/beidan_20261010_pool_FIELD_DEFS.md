# 北单2026-10-10开售池字段定义（WDL/WL混淆修复版）

> **P0修复说明**：此前版本（commit 8f2468c）错误地将WL（胜负过关）玩法的半球让球
> （±0.5/±1.5/±2.5）与WDL（三向胜平负）记录混合。本版已隔离，WDL记录使用
> WDL页面自身的整数让球。

## 计数口径
- 全池：179场（seq 11-189，2026-10-10，均为足球）
- WDL整数线：179场全部可见（逐行核验，含seq 140/143/169，其整数线=-1）
- WDL完整SP：176场
- WDL SP缺失：3场（seq 140/143/169，有整数线但SP空，保持null）
- WL字段已隔离：179场

> **2026-10-09纠正**：此前23a1520称"176场整数线已核验、3场null"为解析错误。
> 经Codex独立解析原始WDL表确认：179条整数线逐行可见，仅SP为176完整。

## 字段定义

| 字段 | 说明 | 非空率 |
|---|---|---|
| period | 期号（26103） | 179/179 |
| seq | 场号 | 179/179 |
| source_match_id | 源站比赛ID（澳客WDL页无，null） | 0/179 |
| sport | football（本批全足球） | 179/179 |
| event_type | 赛事中文名 | 179/179 |
| home/away | 主/客队中文名 | 179/179 |
| league | 联赛中文名 | 179/179 |
| kickoff | 开球时间（+08:00） | 179/179 |
| **official_integer_handicap** | **WDL官方整数让球**（0/±1/±2/±3），来源WDL页面 | 176/179 |
| official_integer_handicap_source | 来源标识（okooo_WDL_page） | 176/179 |
| official_integer_handicap_first_seen | 真实采集UTC | 176/179 |
| **wl_handicap** | **WL玩法半球线**（隔离字段，不得用于WDL） | 179/179 |
| wl_handicap_note | 隔离说明 | 179/179 |
| wl_handicap_first_seen | WL采集UTC | 179/179 |
| sp_wdl | WDL三向SP {win,draw,lose} | 179/179 |
| sp_first_seen | SP真实采集UTC | 179/179 |
| event_space | BJBet/WDL | 179/179 |
| football_model_eligible | true（本批全足球） | 179/179 |
| status | ok/missing_sp | 179/179 |
| missing_reason | 缺失原因 | - |

## 关键声明
1. **WDL整数让球**来自WDL页面自身（`BJBetMatchPoolOddsList.php?LotteryType=WDL`），
   解析路径见 `beidan_20261010_raw_evidence_full.txt`。
2. **WL半球线**来自WL页面（`LotteryType=WL`），已隔离到 `wl_handicap` 字段，
   **禁止**用于WDL预测或与WDL整数线混合计算。
3. seq 140/143/169 的 `official_integer_handicap=null`（WDL原始页面无此三场）。
4. 采集时间均为真实系统UTC，未使用旧mtime或预设时间。

## 原始证据
- `beidan_20261010_raw_evidence_full.txt`：完整WDL数据表HTML（319,929字符），
  含179条10-10记录，SHA256见文件头。
