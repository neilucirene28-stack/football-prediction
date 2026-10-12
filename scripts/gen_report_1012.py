#!/usr/bin/env python3
"""生成2026-10-12竞彩预测报告（9列表格，三档稳胆排序）。"""
import json

BASE = "/home/hatch/workspace/football-prediction-v2"
PICKS = {'home': '胜', 'draw': '平', 'away': '负'}
HPICKS = {'home': '让胜', 'draw': '让平', 'away': '让负'}
GRADE_W = {'S': 1.0, 'A': 1.0, 'B': 0.85, 'C': 0.7, 'D': 0.0}

def best_1x2(ph, pd, pa):
    return max([('home', ph), ('draw', pd), ('away', pa)], key=lambda x: x[1])

def fmt_row(m):
    r = m["result"]
    der = r.get("derivatives") or {}
    ph, pd, pa = r["p_home"], r["p_draw"], r["p_away"]
    # 胜平负：只留概率最高
    b = best_1x2(ph, pd, pa)
    wdl = f"{PICKS[b[0]]}({b[1]:.3f})"
    # 让球：只留概率最高 + 让平风险
    h = der.get("handicap_1x2") or {}
    hb = best_1x2(h.get("p_home", 0), h.get("p_draw", 0), h.get("p_away", 0))
    hstr = f"{HPICKS[hb[0]]}({hb[1]:.3f})"
    risk = "⚠️" if h.get("p_draw", 0) > 0.25 else ""
    # 比分：首选加粗 + Top3；冷门比分补充
    ts = der.get("top_scores") or []
    bs = " ".join(
        f"**{t['score']}**({t['prob']:.3f})" if i == 0 else f"{t['score']}({t['prob']:.3f})"
        for i, t in enumerate(ts[:3])
    ) or "-"
    upset = (der.get("upset_score") or {})
    upset_s = ""
    top3_scores = {t["score"] for t in (ts[:3] or [])}
    if upset.get("score") and upset["score"] not in top3_scores:
        upset_s = f" 冷门:{upset['score']}({upset['prob']:.3f})"
    # 进球数：最可能单个进球数（用精确分布，不用5+桶）+ 概率 + 期望
    tge = der.get("total_goals_exact") or {}
    if tge:
        # 只取纯数字键
        num_items = [(k, v) for k, v in tge.items()
                     if isinstance(k, str) and k.isdigit() and isinstance(v, (int, float))]
        if num_items:
            bk, bv = max(num_items, key=lambda x: x[1])
            gstr = f"{bk}球({bv:.3f})"
        else:
            gstr = "-"
    else:
        tg = der.get("total_goals") or {}
        num_items = [(k, v) for k, v in tg.items()
                     if isinstance(k, int) and isinstance(v, (int, float))]
        gstr = f"{max(num_items, key=lambda x: x[1])[0]}球({max(num_items, key=lambda x: x[1])[1]:.3f})" if num_items else "-"
    eg = der.get("expected_goals")
    if eg:
        gstr += f" 期望{eg:.2f}"
    # 半全场：只留概率最高
    hf = der.get("half_full_1x2") or {}
    if hf:
        bh = max(hf.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else -1)
        hfstr = f"{bh[0]}({bh[1]:.3f})" if isinstance(bh[1], (int, float)) else "-"
    else:
        hfstr = "-"
    # 官方SP
    nspf, spf = m.get("nspf"), m.get("spf")
    sp = ""
    if nspf:
        sp += f"{nspf['3']}/{nspf['1']}/{nspf['0']}"
    if spf:
        sp += f" 让{nspf and '' or ''}{spf['3']}/{spf['1']}/{spf['0']}"
    sp = sp.strip() or "-"
    # 让球标注
    line = m.get("handicap", 0)
    rq = f"({line:+d})" if isinstance(line, int) else ""
    tier_score = GRADE_W.get(m.get("completeness_grade", "C"), 0.7) * b[1]
    return {
        "num": m["num"], "matchup": f"{m['home']}vs{m['away']}{rq}",
        "wdl": wdl, "hcp": hstr, "scores": bs + upset_s, "goals": gstr,
        "hf": hfstr, "sp": sp, "risk": risk,
        "grade": m.get("completeness_grade", "?"), "tier_score": tier_score,
        "top_prob": b[1],
    }

def main():
    d = json.load(open(f"{BASE}/data/predictions/2026-10-12-jingcai.json"))
    ok, skipped = [], []
    for m in d["matches"]:
        if m["result"].get("status") == "ok":
            ok.append(fmt_row(m))
        else:
            skipped.append(m)
    ok.sort(key=lambda x: -x["tier_score"])

    L = []
    L.append("# 2026-10-12 竞彩预测报告（周一001-010）\n")
    L.append(f"> 生成时间：{d['generated_at']} | 模型版本：{d['model_version']}")
    L.append("> 赛程：500官方在售页（周一001-010，开球10-13 00:00-03:15）；小店火页因网络限制未交叉核对。")
    L.append("> 让平风险列：P(让平)>0.25标⚠️；三档按完整度×首选概率排序。\n")

    # 三档：按完整度×首选概率排序
    tiers = [("第一档（稳胆）", ok[:3]), ("第二档", ok[3:6]), ("第三档（谨慎参考）", ok[6:])]
    for name, grp in tiers:
        if not grp:
            continue
        L.append(f"## {name}\n")
        L.append("| 场次 | 对阵 | 胜平负 | 让球胜平负 | 比分 | 进球数 | 半全场 | 官方SP | 让平风险 | 评级 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for x in grp:
            L.append(f"| {x['num']} | {x['matchup']} | {x['wdl']} | {x['hcp']} | {x['scores']} | {x['goals']} | {x['hf']} | {x['sp']} | {x['risk']} | {x['grade']} |")
        L.append("")

    if skipped:
        L.append("## 数据不足（未出预测）\n")
        for m in skipped:
            reason = (m["result"].get("reason") or "")[:60]
            L.append(f"- {m['num']} {m['home']}vs{m['away']}：{reason}（两队无历史战绩数据，AF免费档无权限，已尽力挖掘）")

    L.append("\n## 说明")
    L.append("- 官方让球与SP来自 trade.500.com/jczq/（GBK静态页，11:25抓取）；让球数正=主队受让（客队让球）")
    L.append("- 战绩数据：openfootball（意甲/西甲/英超/巴甲）+ ESPN现挖（瑞典超/挪超/法乙/葡超近56天）")
    L.append("- 报告只含当前预测，不罗列历史比分")

    text = "\n".join(L)
    with open(f"{BASE}/data/predictions/2026-10-12-jingcai-report.md", "w", encoding="utf-8") as f:
        f.write(text)
    print(text[:500])
    print("...")
    print(f"\nReport: {len(ok)} predicted, {len(skipped)} skipped")

if __name__ == "__main__":
    main()
