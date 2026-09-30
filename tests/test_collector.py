"""xiaodianhuo 采集器测试：解析器 + FakePage 桩（零网络）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.collector.sources.xiaodianhuo import (
    XiaoDianHuoSource, _base_name, _beijing_to_iso, _parse_handicap, _num)


class FakePage:
    """实现 tab 解析器用到的 page.locator("body").inner_text()
    以及亚指大小球子 tab 的 get_by_text 点击。"""

    def __init__(self, body_text, ou_text=None):
        self._body = body_text
        self._ou = ou_text

    def locator(self, sel):
        assert sel == "body", sel
        page = self

        class Loc:
            def inner_text(self, timeout=None):
                return page._body
        return Loc()

    def get_by_text(self, text, exact=False):
        page = self

        class Hit:
            @property
            def first(self):
                return self

            def click(self, timeout=None):
                if text == "大小球" and page._ou is not None:
                    page._body = page._ou
        return Hit()

    def wait_for_timeout(self, ms):
        pass


def _body(*tokens):
    """div 渲染的 body 文本：单元格之间空行分隔。"""
    return "\n\n".join(tokens)


def src():
    return XiaoDianHuoSource.__new__(XiaoDianHuoSource)


def test_beijing_to_iso():
    assert "+08:00" in _beijing_to_iso("2026-10-02 19:35:00")
    assert _beijing_to_iso("") is None
    assert _beijing_to_iso("not-a-date") is None


def test_parse_handicap():
    assert _parse_handicap("半/一") == 0.75
    assert _parse_handicap("一/球半") == 1.25
    assert _parse_handicap("平手") == 0.0
    assert _parse_handicap("受一球") == -1.0
    assert _parse_handicap("0.5") == 0.5
    assert _parse_handicap("xx") is None
    assert _num("1.95") == 1.95
    assert _num("高水") is None


def test_base_name():
    assert _base_name("朝鲜女足") == "朝鲜"
    assert _base_name("朝鲜女") == "朝鲜"
    assert _base_name("日本男足") == "日本"
    assert _base_name("阿森纳") == "阿森纳"


def test_parse_card_vs_line():
    # 真实卡片文本：无日期（日期在页面头），队名缩写，赔率行带让球值
    card = ("亚运女足\n14:00\n5条情报\n朝鲜女\nvs\n中国女\n2001\n"
            "0\n1.32 4.25 7.30\n朝鲜队阵容厚度占优！")
    m = src()._parse_card(card, "2026-09-29")
    assert m["home_team"] == "朝鲜女"
    assert m["away_team"] == "中国女"
    assert m["competition"] == "亚运女足"
    assert m["odds"] == {"home": 1.32, "draw": 4.25, "away": 7.30}
    assert m["kickoff_at"].startswith("2026-09-29T14:00")
    assert m["external_id"] == ""  # 点击进详情后回填


def test_parse_card_rejects_junk():
    assert src()._parse_card("广告横幅", "2026-09-29") is None


def test_history_tab_grouped():
    # div token 流：主队近 → 客队近 → 交锋；行以日期 token 为锚点
    body = _body(
        "朝鲜女足", "主队近10场6胜2平2负，主场3胜1平1负",
        "亚运女", "26-09-25", "朝鲜女足", "11", "5-1", "中国台北女足", "42", "赢",
        "亚运女", "26-09-21", "韩国女足", "18", "0-3", "朝鲜女足", "11", "赢",
        "中国女足", "客队近10场4胜3平3负",
        "亚运女", "26-09-25", "中国女足", "13", "1-1", "越南女足", "30", "平",
        "交锋",
        "亚运女", "23-10-06", "中国女足", "13", "1-4", "朝鲜女足", "11", "输",
    )
    d = src()._parse_history_tab(FakePage(body), "朝鲜", "中国")
    h0, h1 = d["home_recent"][0], d["home_recent"][1]
    assert (h0["gf"], h0["ga"], h0["venue"]) == (5, 1, "H")
    assert (h1["gf"], h1["ga"], h1["venue"]) == (3, 0, "A")
    a0 = d["away_recent"][0]
    assert (a0["gf"], a0["ga"], a0["venue"]) == (1, 1, "H")
    assert d["h2h"] == [{"gf": 4, "ga": 1}]


def test_history_tab_h2h_summary_not_section():
    # 交锋区内的"主队近10场…胜…平…负"只是摘要行，不能切回 home section
    body = _body(
        "交锋", "主队近10场6胜1平3负，主场1胜1平2负",
        "亚洲杯女", "26-03-09", "朝鲜女足", "9", "1-2", "中国女足", "17", "输",
    )
    d = src()._parse_history_tab(FakePage(body), "朝鲜", "中国")
    assert d["h2h"] == [{"gf": 1, "ga": 2}]
    assert "home_recent" not in d


def test_history_tab_no_section_fallback():
    # 无分组标题时回退到队名匹配
    body = _body("亚运女", "26-09-20", "朝鲜女足", "11", "2-1", "日本女足",
                 "20", "赢")
    d = src()._parse_history_tab(FakePage(body), "朝鲜", "中国")
    r = d["home_recent"][0]
    assert (r["gf"], r["ga"], r["venue"]) == (2, 1, "H")


def test_europe_tab():
    body = _body("典型指数",
                 "百家平均", "2.01", "3.45", "3.60", "1.98", "3.50", "3.70", ">",
                 "竞彩官方", "1.95", "3.60", "4.20", ">")
    d = src()._parse_europe_tab(FakePage(body))
    assert d["odds"] == {"home": 1.98, "draw": 3.50, "away": 3.70}
    assert d["opening_odds"] == {"home": 2.01, "draw": 3.45, "away": 3.60}
    assert d["odds_source"] == "百家平均"


def test_europe_tab_fallback_jc():
    body = _body("典型指数", "竞彩官方", "1.95", "3.60", "4.20",
                 "1.90", "3.55", "4.30", ">")
    d = src()._parse_europe_tab(FakePage(body))
    assert d["odds"] == {"home": 1.90, "draw": 3.55, "away": 4.30}
    assert d["odds_source"] == "竞彩官方"


def test_asia_tab():
    # 引擎口径：负数=主让
    body = _body("典型亚盘", "Bet365", "0.95", "半/一", "0.85",
                 "0.90", "一球", "1.00", ">")
    ou = _body("典型大小球", "Bet365", "0.95", "2.5", "0.85",
               "0.90", "2.5", "1.00", ">")
    d = src()._parse_asia_tab(FakePage(body, ou_text=ou))
    a = d["asian"]
    assert a["opening_handicap"] == -0.75
    assert a["handicap"] == -1.0
    assert a["opening_home_water"] == 0.95
    assert a["home_water"] == 0.90
    assert a["source"] == "Bet365"
    assert d["ou_line"] == 2.5
    assert d["over_under"]["over_water"] == 0.90
    assert d["over_under"]["under_water"] == 1.00


def test_lineup_tab_no_data_ok():
    d = src()._parse_lineup_tab(FakePage(_body("阵容", "首发", "替补")))
    assert d == {}


def test_lineup_tab_empty():
    # 伤停子 tab 无结构化数据 → 如实返回空，不编造
    body = _body("阵容", "伤停", "密报", "内参")
    d = src()._parse_lineup_tab(FakePage(body))
    assert d == {}


def test_header_teams_prefers_detail_names():
    toks = ["19°", "局部有云/雨", "日联杯", "09-29 18:00", "枥木大平",
            "€620万 日职乙11", "距比赛开始", "06:05:34", "广岛三箭",
            "€2010万 日职联1", "动画直播", "概况", "阵容", "战绩"]
    h, a = src()._header_teams(toks)
    assert (h, a) == ("枥木大平", "广岛三箭")


def test_header_teams_fallback_when_missing():
    assert src()._header_teams(["概况", "阵容", "战绩"]) == (None, None)
