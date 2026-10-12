#!/usr/bin/env python3
"""竞彩 2026-10-10 λ修正版：用挖到的欧洲历史数据（euro_history_dig.json，22队47场）
修正λ，重跑17场预测（009除外）。v2.8链路，D级照常输出（用户指令）。"""
import sys, json, importlib.util
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

# 复用 final 脚本（内含 D 级门控绕过 + build_payload/main）
spec = importlib.util.spec_from_file_location(
    "final1010",
    "/home/hatch/workspace/football-prediction-v2/scripts/predict_jingcai_1010_final.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# ---- 欧洲历史数据 → 引擎近况格式 ----
_DIG = json.load(open("/home/hatch/workspace/football-prediction-v2/"
                       "data/daily/2026-10-10/euro_history_dig.json"))
_DIG_EN = {}
for _team, _ms in _DIG.items():
    if not _ms:
        continue
    _names = set(x["home"] for x in _ms) | set(x["away"] for x in _ms)
    _cand = [n for n in _names
             if all(x["home"] == n or x["away"] == n for x in _ms)]
    if _cand:
        _DIG_EN[_team] = _cand[0]

def _dig_form(team_cn, n=10):
    ms = _DIG.get(team_cn) or []
    if not ms:
        return []
    en = _DIG_EN.get(team_cn)
    out = []
    for x in sorted(ms, key=lambda z: z.get("date", ""), reverse=True):
        is_home = (x["home"] == en) if en else False
        gf = int(x["hs"]) if is_home else int(x["as"])
        ga = int(x["as"]) if is_home else int(x["hs"])
        out.append({"gf": gf, "ga": ga, "venue": "N",
                    "date": x.get("date", ""),
                    "opp": x["away"] if is_home else x["home"]})
        if len(out) >= n:
            break
    return out

_orig_get_form = mod.get_form
def get_form2(team_cn, n=10):
    r = _orig_get_form(team_cn, n)  # J1/K1 footballcharts 优先
    if r:
        return r
    dg = _dig_form(team_cn, n)      # 欧洲球队用挖到的数据
    if dg:
        return dg
    return r
mod.get_form = get_form2

# ---- 跑 ----
mod.main()
print("DIG teams used:", len(_DIG_EN), flush=True)
