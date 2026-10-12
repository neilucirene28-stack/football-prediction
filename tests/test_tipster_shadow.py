"""engine/tipster_shadow.py 的单元测试：加载、匹配、对比 verdict 与永不抛错。"""

import json
import os
import tempfile

import pytest

from engine import tipster_shadow as ts


def make_out(direction="主胜", over_under=None, btts=None, exp_goals=None):
    out = {
        "recommendation": {"direction": direction},
        "derivatives": {},
    }
    if over_under is not None:
        out["derivatives"]["over_under"] = {
            "line": over_under[0],
            "over": over_under[1],
            "under": over_under[2],
        }
    if btts is not None:
        out["derivatives"]["btts_full"] = btts
    if exp_goals is not None:
        out["derivatives"]["expected_goals_full"] = exp_goals
    return out


def test_normalize_team():
    assert ts.normalize_team("Arsenal FC") == "arsenal"
    assert ts.normalize_team("Brighton & Hove Albion") == "brighton and hove albion"
    assert ts.normalize_team("  Malmo FF  ") == "malmo ff"


def test_split_match():
    assert ts.split_match("Coventry City vs Newcastle United") == (
        "Coventry City", "Newcastle United")
    assert ts.split_match("垃圾") is None


def test_find_matches_exact_and_swapped():
    picks = [
        {"tipster": "eaglepredict", "match": "Coventry City vs Newcastle United",
         "market": "1X2", "pick": "X2", "odds": 1.4},
        {"tipster": "eaglepredict", "match": "Levante FC vs Sevilla",
         "market": "OU", "pick": "O1.5", "odds": 1.35},
    ]
    hits = ts.find_matches("Coventry City", "Newcastle United", picks)
    assert len(hits) == 1 and not hits[0][1]
    hits = ts.find_matches("Newcastle United", "Coventry City", picks)
    assert len(hits) == 1 and hits[0][1]  # 反位也命中
    hits = ts.find_matches("Levante", "Sevilla", picks)
    assert len(hits) == 1  # FC 后缀被规范化掉
    assert ts.find_matches("A", "B", picks) == []


def test_1x2_verdicts():
    out = make_out("客胜")
    r = ts.compare_one("1X2", "X2", out)
    assert r["verdict"] == "partial" and r["engine_pick"] == "2"
    r = ts.compare_one("1X2", "2", out)
    assert r["verdict"] == "agree"
    r = ts.compare_one("1X2", "1", out)
    assert r["verdict"] == "disagree"
    r = ts.compare_one("1X2", "1X", out)
    assert r["verdict"] == "disagree"


def test_ou_same_line():
    out = make_out(over_under=(1.5, 0.72, 0.28))
    assert ts.compare_one("OU", "O1.5", out)["verdict"] == "agree"
    assert ts.compare_one("OU", "Under 1.5", out)["verdict"] == "disagree"


def test_ou_expected_goals_proxy():
    out = make_out(exp_goals=3.1)
    assert ts.compare_one("OU", "O2.5", out)["verdict"] == "agree"
    assert ts.compare_one("OU", "U2.5", out)["verdict"] == "disagree"
    out2 = make_out(exp_goals=2.55)  # 与盘口差距在中性带内
    assert ts.compare_one("OU", "O2.5", out2)["verdict"] == "disagree"


def test_btts():
    out = make_out(btts=0.62)
    assert ts.compare_one("BTTS", "BTTS-Y", out)["verdict"] == "agree"
    assert ts.compare_one("BTTS", "No", out)["verdict"] == "disagree"


def test_acca_skipped():
    assert ts.compare_one("ACCA", "ACCA", make_out())["verdict"] == "skipped"


def test_compare_all_summary():
    out = make_out("客胜", over_under=(1.5, 0.72, 0.28))
    picks = [
        ({"market": "1X2", "pick": "2", "odds": 1.8}, False),
        ({"market": "1X2", "pick": "1", "odds": 2.1}, False),
        ({"market": "OU", "pick": "O1.5", "odds": 1.35}, False),
    ]
    report = ts.compare_all(out, picks)
    assert report["status"] == "ok"
    assert report["summary"] == {
        "agree": 2, "partial": 0, "disagree": 1,
        "neutral": 0, "skipped": 0,
    }


def test_attach_shadow_never_raises_and_writes_log():
    out = make_out("主胜")
    with tempfile.TemporaryDirectory() as td:
        picks_path = os.path.join(td, "picks.jsonl")
        with open(picks_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "tipster": "eaglepredict", "match": "Arsenal vs Chelsea",
                "market": "1X2", "pick": "1", "odds": 1.9,
                "posted_at": "2026-10-12T14:00:00+08:00",
            }, ensure_ascii=False) + "\n")
        log_path = os.path.join(td, "shadow.jsonl")
        ts.attach_shadow(out, picks_path, "Arsenal", "Chelsea",
                         tipster="eaglepredict", log_path=log_path)
        assert out["tipster_shadow"]["status"] == "ok"
        assert out["tipster_shadow"]["summary"]["agree"] == 1
        assert os.path.exists(log_path)
        # 路径不存在也永不抛错
        out2 = make_out()
        ts.attach_shadow(out2, "/no/such/file.jsonl", "A", "B")
        assert out2["tipster_shadow"]["status"] == "skipped"


def test_load_picks_filters_tipster():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "p.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"tipster": "eaglepredict", "match": "A vs B"}\n')
            f.write('{"tipster": "other", "match": "A vs B"}\n')
            f.write('not json\n')
        assert len(ts.load_picks(p, "eaglepredict")) == 1
