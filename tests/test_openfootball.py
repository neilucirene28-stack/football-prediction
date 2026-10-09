"""openfootball 采集器测试：纯解析逻辑 + tmp 本地仓库（零网络）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "sources"))

import openfootball as of  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    with open(FIX / name, encoding="utf-8") as f:
        return json.load(f)


def _fake_repo(tmp_path):
    """搭一个最小本地数据仓库：2026/br.1.json + 2026-27/en.1.json。"""
    (tmp_path / "2026").mkdir()
    (tmp_path / "2026-27").mkdir()
    (tmp_path / "2026" / "br.1.json").write_text(
        (FIX / "openfootball_br1_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "2026-27" / "en.1.json").write_text(
        json.dumps({"name": "English Premier League 2026-27", "matches": [
            {"round": "Matchday 1", "date": "2026-08-21", "time": "20:00",
             "team1": "Arsenal FC", "team2": "Coventry City FC",
             "score": {"ht": [2, 0], "ft": [3, 0]}},
        ]}),
        encoding="utf-8",
    )
    return tmp_path


def test_parse_fixture():
    # fixture: 4 条 -> 1条队名缺失跳过, 1条未赛默认排除
    ms = of._parse_matches(_load("openfootball_br1_sample.json"), "br.1", "2026")
    assert len(ms) == 3
    played = [m for m in ms if m["played"]]
    assert len(played) == 2
    # dict 形 score: 全场+半场
    m0 = ms[0]
    assert m0["date"] == "2026-01-28" and m0["round"] == "Matchday 1"
    assert m0["home"] == "CA Mineiro" and m0["away"] == "SE Palmeiras"
    assert m0["score_home"] == 2 and m0["score_away"] == 2
    assert m0["ht_home"] == 1 and m0["ht_away"] == 1
    # list 形 score: 全场, 半场 None
    m1 = ms[1]
    assert m1["score_home"] == 0 and m1["score_away"] == 1
    assert m1["ht_home"] is None and m1["ht_away"] is None
    # 未赛: played False, 比分 None
    m2 = ms[2]
    assert m2["played"] is False
    assert m2["score_home"] is None and m2["score_away"] is None
    assert m2["league"] == "br.1" and m2["season"] == "2026"


def test_get_results_filters_unplayed(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "DATA_DIR", _fake_repo(tmp_path))
    rs = of.get_results("巴甲", "2026")
    assert len(rs) == 2  # 未赛默认排除
    assert all(m["played"] for m in rs)
    rs_all = of.get_results("br.1", "2026", include_unplayed=True)
    assert len(rs_all) == 3
    # 不存在的联赛/赛季 -> []
    assert of.get_results("巴甲", "2099") == []
    assert of.get_results("xx.9", "2026") == []


def test_season_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "DATA_DIR", _fake_repo(tmp_path))
    # 传 "2026" 自动解析到 "2026-27" 目录
    rs = of.get_results("英超", "2026")
    assert len(rs) == 1
    assert rs[0]["season"] == "2026-27"
    assert rs[0]["home"] == "Arsenal FC"
    # 直接传完整赛季名也行
    assert len(of.get_results("en.1", "2026-27")) == 1
    # 中文名映射
    assert of.LEAGUE_CODES["巴西甲"] == "br.1"
    assert of.LEAGUE_CODES["欧冠"] == "uefa.cl"


def test_list_leagues(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "DATA_DIR", _fake_repo(tmp_path))
    leagues = of.list_leagues()
    codes = {lg["code"]: lg for lg in leagues}
    assert set(codes) == {"br.1", "en.1"}
    assert codes["br.1"]["name"] == "巴甲"
    assert codes["br.1"]["seasons"] == ["2026"]
    assert codes["en.1"]["seasons"] == ["2026-27"]
    # DATA_DIR 不存在 -> []
    monkeypatch.setattr(of, "DATA_DIR", tmp_path / "nope")
    assert of.list_leagues() == []


def test_sync_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(of, "DATA_DIR", tmp_path)
    monkeypatch.setattr(of, "CLUBS_DIR", tmp_path / "clubs")
    st = of.sync()
    assert st["football.json"]["ok"] is False
    assert st["football.db"]["ok"] is False
