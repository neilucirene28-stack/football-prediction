#!/usr/bin/env python3
"""生成2026-10-11竞彩预测报告"""
import json
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))
PICKS = {'home': '胜', 'draw': '平', 'away': '负'}
HPICKS = {'home': '让胜', 'draw': '让平', 'away': '让负'}

def fmt_scores(ts):
    if not ts:
        return "-"
    return " ".join([f"**{t['score']}**({t['prob']:.3f})" if i == 0 else f"{t['score']}({t['prob']:.3f})"
                     for i, t in enumerate(ts[:3])])

def main():
    d = json.load(open("/home/hatch/workspace/football-prediction-v2/data/predictions/2026-10-11-jingcai.json"))
    lines = []
    lines.append("# 2026-10-11 竞彩预测报告（28场）\n")
    lines.append(f"> 生成时间：{d['generated_at']} | 模型版本：{d['model_version']}")
    lines.append("> 001/002/003已开赛，引擎拒绝赛后预测；022本菲卡只有让球盘。")
    lines.append("> 让平风险列：P(让平)>0.25标⚠️。\n")
    lines.append("| 场次 | 对阵 | 胜平负 | 让球胜平负 | 比分Top3 | 进球数 | 半全场 | 官方SP | 让平风险 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for m in d["matches"]:
        r = m["result"]
        der = r.get("derivatives") or {}
        ph, pd, pa = r["p_home"], r["p_draw"], r["p_away"]
        # 胜平负首选
        wdl_best = max([("home", ph), ("draw", pd), ("away", pa)], key=lambda x: x[1])
        wdl_str = f"{PICKS[wdl_best[0]]}({wdl_best[1]:.3f})"
        # 让球
        h = der.get("handicap_1x2")
        if h:
            h_best = max([("home", h["p_home"]), ("draw", h["p_draw"]), ("away", h["p_away"])], key=lambda x: x[1])
            h_str = f"{HPICKS[h_best[0]]}({h_best[1]:.3f})"
            risk = "⚠️" if h["p_draw"] > 0.25 else ""
        else:
            h_str, risk = "-", ""
        # 比分
        bs = fmt_scores(der.get("top_scores"))
        # 进球数
        tg = der.get("total_goals") or {}
        exp_g = der.get("expected_goals", 0)
        if tg:
            # 找最可能的进球数
            best_g = max(tg.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0)
            g_str = f"{best_g[0]}球({best_g[1]:.3f})" if isinstance(best_g[1], (int, float)) else "-"
        else:
            g_str = "-"
        g_str += f" 期望{exp_g:.2f}" if exp_g else ""
        # 半全场
        hf = der.get("half_full_1x2") or {}
        if hf:
            hf_best = max(hf.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0)
            hf_str = f"{hf_best[0]}({hf_best[1]:.3f})" if isinstance(hf_best[1], (int, float)) else "-"
        else:
            hf_str = "-"
        # SP
        nspf = m.get("nspf")
        spf = m.get("spf")
        sp_str = ""
        if nspf:
            sp_str += f"{nspf['3']}/{nspf['1']}/{nspf['0']}"
        if spf:
            sp_str += f" 让{spf['3']}/{spf['1']}/{spf['0']}"
        if not sp_str:
            sp_str = "-"

        lines.append(f"| {m['num']} | {m['home']}vs{m['away']} | {wdl_str} | {h_str} | {bs} | {g_str} | {hf_str} | {sp_str} | {risk} |")

    lines.append("\n## 说明")
    lines.append("- 周日001/002/003已开赛（13:00/14:00/15:00），引擎按纪律拒绝赛后预测")
    lines.append("- 周日022本菲卡vs吉马良斯只有让球市场，未出胜平负预测")
    lines.append("- 数据缺失场次已标注form场次，λ用引擎默认")

    with open("/home/hatch/workspace/your_files/2026-10-11-竞彩-预测报告.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Report written: {len(d['matches'])} matches")

if __name__ == "__main__":
    main()
