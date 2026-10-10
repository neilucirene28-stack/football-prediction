"""Separate Beijing calendar dates and expose selected vs diagnostic models."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path

from beidan_bd1.runner import run_shadow_pool

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "outputs/l1_prospective/20261009T143301921759Z"
MANIFEST_SHA = "fd34f7edc0df7d7adde4660cb42f1ce3baf7d659dc1b040460e9630a1f206b13"
BEIJING = timezone(timedelta(hours=8))


def percentages(vector):
    return "/".join(f"{vector[k] * 100:.1f}%" for k in ("胜", "平", "负"))


def top_scores(vector, n=3):
    return "、".join(f"{k} ({v * 100:.1f}%)" for k, v in sorted(vector.items(), key=lambda x: -x[1])[:n])


def main():
    now = datetime.now(timezone.utc)
    if hashlib.sha256((BUNDLE / "freeze_manifest.json").read_bytes()).hexdigest() != MANIFEST_SHA:
        raise ValueError("independently retained freeze manifest digest differs")
    manifest = json.loads((BUNDLE / "freeze_manifest.json").read_text())
    for name, metadata in manifest["files"].items():
        raw = (BUNDLE / name).read_bytes()
        if len(raw) != metadata["size_bytes"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ValueError("frozen input bytes differ")
    frozen = json.loads((BUNDLE / "report.json").read_text())
    history = [json.loads(line) for line in (BUNDLE / "history.jsonl").read_text().splitlines() if line]
    source = ROOT.parent / "data/exports/beidan_current_pool_20261009.jsonl"
    rows = [json.loads(line) for line in source.read_text().splitlines() if line]
    if len(rows) != 193 or len({r["match_id"] for r in rows}) != 193:
        raise ValueError("original current pool count or duplicate identity differs")
    day = now.astimezone(BEIJING).date()
    today = [r for r in rows if r["sport"] == "football"
             and datetime.fromisoformat(r["kickoff_at"]).astimezone(BEIJING).date() == day]
    fixtures = [{"period": r["period"], "match_id": f"{r['period']}-{r['seq']}",
                 "sport": "football", "kickoff_at": datetime.fromisoformat(r["kickoff_at"]).isoformat(),
                 "competition_family": None, "official_handicap": None,
                 "sources": [{"name": "pinned_current_pool_archive_read_today",
                              "source_match_id": None, "available_at": now.isoformat(), "status": "ok"}],
                 # No approved fixture/history mapping: these stay observation only.
                 "input_sources": None} for r in today]
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "outputs/today_calendar_shadow" / run_id
    if today:
        report = run_shadow_pool(fixtures=fixtures, history=history, root=output,
                                 expected_total=len(today), period="26103", run_id=run_id)
    else:
        report = {"matches": [], "asof_at": now.isoformat(), "offered_n": 0}
    audit = {"verified_at": now.isoformat(), "source_commit": "373be6b66f916fa5940dd5a5de50cf21e56bb901",
             "current_pool_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "scope": "Beijing calendar-day subset, not independently verified full official pool",
             "calendar_date": day.isoformat(), "calendar_football_n": len(today),
             "saved_prior_n": sum(r["status"] == "saved" for r in report["matches"]),
             "team_strength_prediction_n": 0, "production_eligible": False,
             "source_rows": today, "run_manifest": report}
    (ROOT / "outputs/today_calendar_status.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    text = ["# 北单当前预测：北京时间日期分列", "",
            f"整理时间：{datetime.now(BEIJING).isoformat()}。场名来自未批准的中文来源绑定。", "",
            f"## {day.isoformat()} 今天", "",
            f"归档池有{len(today)}场当天足球；本次真实运行仅为尚未开球场次生成L3根先验，已经开球的比赛记入跳过账本。", "",
            "L3只使用341场已核验比分，不包含这两队的实力信息；未知赛事族使用同一根先验，所以相同概率不是两场比赛各自的球队分析。让球/SP未导入，尚不能作为有效球队预测或实战推荐。", "",
            "| 场号 | 比赛 | 开球 | 状态 | L3胜／平／负 | L3前三比分 |",
            "|---|---|---|---|---|---|"]
    lookup = {r["match_id"]: r for r in report["matches"]}
    for row in sorted(today, key=lambda r: int(r["seq"])):
        result = lookup[f"{row['period']}-{row['seq']}"]
        if result["status"] == "saved":
            snapshot = json.loads((output / result["snapshot_path"]).read_text())
            if snapshot["observation_only"] is not True:
                raise ValueError("unbound calendar prior unexpectedly eligible")
            vectors = snapshot["vectors"]
            status, wdl, scores = "赛前观察先验；无球队实力预测", percentages(vectors["wdl"]), top_scores(vectors["score"])
        else:
            status = ("开球已到或已过，未补造赛前预测" if datetime.fromisoformat(row["kickoff_at"]) <= now
                      else "输入核验阻断，未生成预测")
            wdl, scores = "—", "—"
        text.append(f"| {row['seq']} | {row['home']} vs {row['away']} | {row['kickoff_at']} | {status} | {wdl} | {scores} |")
    text.extend(["", "## 2026-10-10 已封存的来源ID研究候选", "",
                 "完整池179场，40场研究候选，139场阻断。当前已选ridge=None（L3冷启动）；ridge=5仅作为预声明L1诊断候选，不能冒充已选模型。0场成对赛果，Brier为空。", "",
                 "| 场号 | 联赛 | 比赛 | 开球（北京） | 已选L3胜／平／负 | L1 ridge=5胜／平／负（诊断） | L1前三比分（诊断） |",
                 "|---|---|---|---|---|---|---|"])
    roster = {r["match_id"]: r for r in json.loads((ROOT / "outputs/20261010_179_shadow.json").read_text())["predictions"]}
    for match in frozen["folds"][0]["matches"]:
        if match["status"] != "predicted_research":
            continue
        row = roster[match["match_id"]]
        chosen = match["candidate"]["vectors"]
        l1 = match["alternatives"]["5.0"]["vectors"]
        text.append(f"| {row['seq']} | {row['league']} | {row['home']} vs {row['away']} | {row['kickoff_at']} | {percentages(chosen['wdl'])} | {percentages(l1['wdl'])} | {top_scores(l1['score'])} |")
    text.extend(["", f"本表读自原冻结概率；freeze_manifest SHA256：`{MANIFEST_SHA}`。今天的8场日历子池与明天的179场冻结是两个范围，不合并分母。", ""])
    path = ROOT / "outputs/predictions_20261009_20261010.md"
    path.write_text("\n".join(text))
    print(json.dumps({"table": str(path), "today_football_n": len(today),
                      "today_saved_prior_n": audit["saved_prior_n"], "tomorrow_research_n": frozen["predicted_n"],
                      "production_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
