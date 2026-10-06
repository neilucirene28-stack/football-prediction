"""北单SP数据源(beidan.py)解析测试：合成载荷，零网络。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "collector" / "sources"
sys.path.insert(0, str(SRC_DIR))

import beidan


WDL_HTML = """<html><body><table id="TableBorder">
<tr><td colspan="9">已经隐藏 0 场比赛</td></tr>
<tr><td>序号隐藏</td><td>赛事</td><td>对阵</td><td>比赛时间</td><td>澳客停售</td>
<td>SP变化</td><td>胜</td><td>平</td><td>负</td></tr>
<tr><td>18</td><td>友谊赛</td><td>韩国 (-1) VS 乌兹别克斯坦</td>
<td>10-06 19:00</td><td>19:00</td><td>查看</td>
<td>2.95</td><td>4.07</td><td>2.40</td></tr>
<tr><td>19</td><td>友谊赛</td><td>中国 VS 塔吉克斯坦</td>
<td>10-06 19:35</td><td>19:35</td><td>查看</td>
<td>3.52</td><td>--</td><td>2.37</td></tr>
</table></body></html>"""

SCORE_HTML = """<html><body><table id="TableBorder">
<tr><td>序号隐藏</td><td>赛事</td><td>对阵</td><td>比赛时间</td><td>澳客停售</td>
<td>SP变化</td><td>胜其他</td><td>1:0</td><td>2:0</td></tr>
<tr><td>平其他</td><td>0:0</td><td>1:1</td></tr>
<tr><td>负其他</td><td>0:1</td></tr>
<tr><td>18</td><td>友谊赛</td><td>韩国 VS 乌兹别克斯坦</td>
<td>10-06 19:00</td><td>19:00</td><td>查看</td>
<td>22.44</td><td>11.93</td><td>10.05</td></tr>
<tr><td>496.55</td><td>21.17</td><td>11.34</td></tr>
<tr><td>214.92</td><td>26.21</td></tr>
</table></body></html>"""

KAIJIANG_HTML = """<html><body><table>
<tr><td class="noborder">2</td>
<td><span class="namebox">欧国联</span></td>
<td>10-06 00:00</td>
<td><a href="/soccer/match/1/history/">塞浦路斯</a></td>
<td><a href="/soccer/match/1/history/">拉脱维亚</a></td>
<!--<td><a href="//www.okooo.com/soccer/match/1/odds/">赔率</a></td>-->
<td>0-0</td><td class="border2">2-1</td>
<td><span class="font_red">-1</span></td>
<td><b class="font_red">1</b></td><td class="border2">2.935</td>
<td><b class="font_red">2:1</b></td><td class="border2">7.455</td>
<td><b class="font_red">3</b></td><td class="border2">4.419</td>
<td><b class="font_red">1-3</b></td><td class="border2">3.897</td>
<td><b class="font_red">上单</b></td><td>3.339</td></tr>
</table></body></html>"""

W500_HTML = """<html><body><table>
<tr><td>18</td><td>友谊赛</td><td>18:50</td><td>[世23]</td><td>韩国</td>
<td>-1</td><td>乌兹别克斯坦</td><td>[世58]</td>
<td>1.64</td><td>3.82</td><td>4.71</td><td>析</td></tr>
</table></body></html>"""


class TestParseMatchup:
    def test_with_handicap(self):
        h, a, hc = beidan.parse_matchup("韩国 (-1) VS 乌兹别克斯坦")
        assert (h, a, hc) == ("韩国", "乌兹别克斯坦", -1.0)

    def test_decimal_handicap(self):
        h, a, hc = beidan.parse_matchup("赖于福斯 (+1.5) VS 斯达")
        assert (h, a, hc) == ("赖于福斯", "斯达", 1.5)

    def test_no_handicap(self):
        h, a, hc = beidan.parse_matchup("韩国 VS 乌兹别克斯坦")
        assert (h, a, hc) == ("韩国", "乌兹别克斯坦", None)


class TestWdlPage:
    def test_parse(self):
        rows = beidan.get_sp_page("WDL", html=WDL_HTML)
        assert len(rows) == 2
        r = rows[0]
        assert r["seq"] == "18" and r["home"] == "韩国"
        assert r["away"] == "乌兹别克斯坦" and r["handicap"] == -1.0
        assert r["sp"] == {"胜": 2.95, "平": 4.07, "负": 2.40}
        assert r["kickoff"].endswith("10-06 19:00")

    def test_missing_sp_is_none(self):
        rows = beidan.get_sp_page("WDL", html=WDL_HTML)
        assert rows[1]["sp"]["平"] is None
        assert rows[1]["handicap"] is None


class TestScorePage:
    def test_three_row_block(self):
        rows = beidan.get_sp_page("Score", html=SCORE_HTML)
        assert len(rows) == 1
        sp = rows[0]["sp"]
        assert sp["胜其他"] == 22.44 and sp["1:0"] == 11.93
        assert sp["平其他"] == 496.55 and sp["0:0"] == 21.17
        assert sp["负其他"] == 214.92 and sp["0:1"] == 26.21


class TestKaijiang:
    def test_parse(self):
        rows = beidan.get_kaijiang("26102", html=KAIJIANG_HTML)
        assert len(rows) == 1
        r = rows[0]
        assert r["home"] == "塞浦路斯" and r["away"] == "拉脱维亚"
        assert r["half_score"] == "0-0" and r["full_score"] == "2-1"
        assert r["handicap"] == -1.0
        assert r["rq_result"] == "让平" and r["rq_sp"] == 2.935
        assert r["score_result"] == "2:1" and r["score_sp"] == 7.455
        assert r["goals"] == 3 and r["goals_sp"] == 4.419
        assert r["half_full"] == "平胜" and r["half_full_sp"] == 3.897
        assert r["ou"] == "上单" and r["ou_sp"] == 3.339

    def test_comment_cell_stripped(self):
        # 注释掉的赔率列不应被解析为数据列
        rows = beidan.get_kaijiang("26102", html=KAIJIANG_HTML)
        assert rows[0]["half_score"] == "0-0"


class TestW500:
    def test_header_parse(self):
        rows = beidan.parse_500_headers(W500_HTML)
        assert len(rows) == 1
        r = rows[0]
        assert r["home"] == "韩国" and r["away"] == "乌兹别克斯坦"
        assert r["handicap"] == -1.0
        assert (r["sp_w"], r["sp_d"], r["sp_l"]) == (1.64, 3.82, 4.71)


class TestMerge:
    def test_get_all_sp_merges(self, monkeypatch):
        pages = {"WDL": WDL_HTML, "Score": SCORE_HTML}
        monkeypatch.setattr(beidan, "PLAY_TYPES", ("WDL", "Score"))
        merged = beidan.get_all_sp(html_pages=pages)
        k = ("韩国", "乌兹别克斯坦")
        assert k in merged
        assert merged[k]["sp_wdl"]["胜"] == 2.95
        assert merged[k]["sp_score"]["1:0"] == 11.93
