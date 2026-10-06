#!/usr/bin/env python3
"""
volume_weight 影子特征的 walk-forward 验证桩。

TODO（用户要求：验证有效果后才能进生产）：
1. 从 data/daily/*/matchbook_volume.json 收集历史 volume_weight 快照
   （要求：快照时间 < 比赛开球时间，防泄漏）。
2. 与 settlements / 复盘结果对齐，计算：
   - 高 volume_weight 场次的模型 Brier vs 低 volume_weight 场次
   - volume_weight 作为市场信号可信度加权的增益（CLV 框架）
3. 只有 walk-forward 显示稳定增益，才考虑接入 engine 融合。

当前状态：影子模式，只记录不进生产。
生产权重/融合/门控一律不动（见 engine/，本次任务未修改）。

参考：
- collector/sources/matchbook.py::volume_weight（影子特征定义）
- scripts/daily_fetch.py 第 7 节（每日快照落盘）
- 2026-09-30 下注流水调研结论：成交量作"市场信号可信度权重"有理论支撑，
  但"钱去了哪"本身几乎无增量价值（赔率已把资金吃进去）。
"""

import json
import os
import sys

BASE_DIR = os.path.expanduser("~/workspace/football-prediction-v2")
DATA_DIR = os.path.join(BASE_DIR, "data", "daily")


def collect_volume_history():
    """收集所有历史 matchbook_volume.json 快照。"""
    rows = []
    if not os.path.isdir(DATA_DIR):
        return rows
    for day in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, day, "matchbook_volume.json")
        if not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for r in data:
            r["_snapshot_day"] = day
            rows.append(r)
    return rows


def main():
    rows = collect_volume_history()
    print(f"历史 volume 快照行数: {len(rows)}")
    if not rows:
        print("暂无数据：等 scripts/daily_fetch.py 跑几天后再验证。")
        return
    # TODO: 对齐 settlements，做 walk-forward 评估
    print("TODO: 对齐 settlements + walk-forward 评估（见文件头说明）")


if __name__ == "__main__":
    main()
