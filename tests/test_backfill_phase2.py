"""backfill_phase2.to_recent 的 venue 编码回归测试。

回归对象（2026-10-07 修复）：to_recent 曾输出小写 venue "home"/"away"，
而 engine/strengths.attack_defense 只认 "H"/"A"/"N"，导致回填的 225 场近况
被 venue='H'/'A' 过滤条件静默丢弃（评级退化为 1.0，生产链路不受影响）。

策略（选其一并写明）：在源头 to_recent 做转换（输出 "H"/"A"），不改 engine；
engine 层保留严格过滤语义。测试保证：
  1. to_recent 输出的 venue 恒为 "H"/"A"（不再出现小写）；
  2. 输出能被 attack_defense(venue="H"/"A") 实际消费（过滤后 n>0），
     即小写输入不再静默丢弃。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_phase2 import to_recent
from engine.strengths import attack_defense

_SAMPLE = [
    {"venue": "home", "hg": 2, "ag": 1, "home": "主队A", "away": "客队B",
     "date": "2026-09-01"},
    {"venue": "away", "hg": 0, "ag": 0, "home": "主队C", "away": "主队A",
     "date": "2026-09-05"},
    {"venue": "home", "hg": 1, "ag": 3, "home": "主队A", "away": "客队D",
     "date": "2026-09-10"},
    # 比分缺失的场次应被跳过
    {"venue": "home", "hg": None, "ag": None, "home": "主队A",
     "away": "客队E", "date": "2026-09-12"},
]


def test_to_recent_venue_encoding_is_uppercase():
    """输出 venue 必须是 engine 约定的 'H'/'A'，不允许小写。"""
    out = to_recent(_SAMPLE, "home")
    assert len(out) == 3  # 缺失比分场次跳过
    assert {r["venue"] for r in out} == {"H", "A"}
    assert all(r["venue"] in ("H", "A", "N") for r in out)
    # 显式断言：不允许旧的小写编码复发
    assert not any(r["venue"] in ("home", "away") for r in out)


def test_to_recent_output_consumed_by_attack_defense():
    """输出的近况必须被 attack_defense 的 venue 过滤实际保留（n>0）。

    这是 bug 的核心：旧输出 venue="home"/"away" 时，下面的 n 恒为 0，
    近况被静默丢弃，评级退化为 (1.0, 1.0)。
    """
    out = to_recent(_SAMPLE, "home")
    home_rows = [r for r in out if r["venue"] == "H"]
    away_rows = [r for r in out if r["venue"] == "A"]
    _, _, dh = attack_defense(home_rows, 2.7, venue="H")
    _, _, da = attack_defense(away_rows, 2.7, venue="A")
    assert dh["n"] == 2, f"主场近况被丢弃: n={dh['n']}"
    assert da["n"] == 1, f"客场近况被丢弃: n={da['n']}"
    # 有真实近况时评级不应退化为全 1.0
    ah, dfh, _ = attack_defense(home_rows, 2.7, venue="H")
    assert (ah, dfh) != (1.0, 1.0)


def test_to_recent_gf_ga_oriented_to_team_side():
    """gf/ga 按被统计球队视角定向（主队视角：venue=home 的场 gf=hg）。"""
    out = to_recent(_SAMPLE, "home")
    by_date = {r["date"]: r for r in out}
    assert (by_date["2026-09-01"]["gf"], by_date["2026-09-01"]["ga"]) == (2, 1)
    # venue=away 的场：球队是客队，gf 取 ag
    assert (by_date["2026-09-05"]["gf"], by_date["2026-09-05"]["ga"]) == (0, 0)
