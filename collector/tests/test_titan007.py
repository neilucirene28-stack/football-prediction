"""Titan007 数据源解析测试：全部使用离线 fixture，不触网。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector.collector.sources.titan007 import (
    Titan007Source,
    parse_ah_number,
    parse_ou_number,
    MEMBER_ONLY_MODULES,
)

DETAIL_HTML = """<html><head><title>朝鲜女足 VS 中国女足(2026赛季亚运女足)-现场分析-新球体育
</title></head><body>
<script type="text/javascript">
    var scheduleID = 3095429;
    var state = 0;
    var strTime = '2026-09-29 14:00';
</script>
<a href='//zq.titan007.com/cn/cupmatch.aspx?sclassid=405' target='_blank' class="LName">亚运女足</a>
<span class="time">2026-09-29 14:00</span>
</body></html>"""

X12_JS = """var matchname_cn="亚运女足";
var MatchTime="2026,09-1,29,06,00,00";
var hometeam_cn="朝鲜女足";
var guestteam_cn="中国女足";
var hometeamID=2697;
var guestteamID=1933;
var neutrality="1";
var temperature="18℃～19℃";
var game=Array("1129|158236391|Lottery Official|1.32|4.25|7.3|67.05|20.83|12.12|88.51|1.36|4.25|6.3|65.11|20.84|14.06|88.55|0.85|0.95|0.94|2026,09-1,29,02,19,00|竞彩官*|1|0|0.83|0.95|1.09","281|158229660|Bet 365|1.42|3.9|6.5|63.19|23.01|13.8|89.73|1.48|3.75|6|60.93|24.05|15.03|90.17|0.93|0.84|0.89|2026,09-1,29,02,50,00|36*(英国)|1|0|0.89|0.87|0.97");
var gameDetail=Array("158236391^1.36|4.25|6.3|09-29 02:19|0.85|0.95|0.94|2026;1.32|4.25|7.3|09-28 12:00|0.83|0.95|1.09|2026;","158229660^1.48|3.75|6|09-29 02:50|0.93|0.84|0.89|2026;");
"""

ANALYSIS_HTML = """<html><head><title>朝鲜女足 VS 中国女足(2026赛季亚运女足)-数据分析</title></head>
<body><script>
var scheduleID = 3095429;             var h2h_home = 2697;             var h2h_away = 1933;
var v_data=[['26-03-09',629,'女亚杯','#FF9966',2697,'<span title="排名：9">朝鲜女足(中)</span>',1933,'<span title="排名：17">中国女足</span>',1,2,'1-2','',-1,-2,1,2838206,'5','3','//zq.titan007.com/cn/cupmatch.aspx?sclassid=629']];
var h_data=[['26-09-25',405,'亚运女足','#CC6633',2697,'<span title="朝鲜女足  排名:11">朝鲜女足</span>(中)',4412,'<span title="中国台湾女足  排名:40">中国台湾女足</span>',5,1,'3-0','4',1,-2,1,3094456,'10','0','//zq.titan007.com/cn/cupmatch.aspx?sclassid=405'],['26-09-21',405,'亚运女足','#CC6633',4413,'<span>越南女足</span>',2697,'<span>朝鲜女足</span>',0,3,'0-2','',1,-2,1,3094401,'2','7','//zq.titan007.com/cn/cupmatch.aspx?sclassid=405']];
var a_data=[['26-09-25',405,'亚运女足','#CC6633',1933,'<span title="中国女足  排名:16">中国女足</span>(中)',4413,'<span title="越南女足  排名:37">越南女足</span>',0,0,'0-0','3.5',0,-2,-1,3094457,'13','0','//zq.titan007.com/cn/cupmatch.aspx?sclassid=405']];
var h2_data=[];
var a2_data=[];
</script></body></html>"""

ASIAN_HTML = """<html><body><table id="odds">
<tr><td class="lb rb">公司</td><td>初</td><td></td><td></td><td>即时</td><td></td><td></td></tr>
<tr bgcolor="#FFFFFF">
<td width="35" class="lb rb"><input type="checkbox" name="oddsShow" data-id="1" value="0"></td>
<td height="25">澳*</td>
<td><span class='down' companyID='1'></span></td>
<td title="2026-09-27 17:34">0.89</td>
<td title="2026-09-27 17:34" goals="1">一球</td>
<td title="2026-09-27 17:34">0.81</td>
<td oddstype="wholeLastOdds">0.76</td>
<td goals="1" oddstype="wholeLastOdds">一球</td>
<td oddstype="wholeLastOdds">0.94</td>
<td><a href="/changeDetail/handicap.aspx?id=3095429&companyID=1&l=0">指数</a></td>
</tr>
<tr bgcolor="#FFFFFF">
<td width="35" class="lb rb"><input type="checkbox" name="oddsShow" data-id="2" value="0"></td>
<td height="25">Crow*</td>
<td><span class='up' companyID='2'></span></td>
<td>0.70</td><td>一球</td><td>1.00</td>
<td>1.04</td><td>一球/球半</td><td>0.78</td>
<td><a href="/changeDetail/handicap.aspx?id=3095429&companyID=2&l=0">指数</a></td>
</tr>
</table></body></html>"""

OU_HTML = """<html><body><table id="odds">
<tr><td class="lb rb">公司</td><td>初</td><td></td><td></td><td>即时</td><td></td><td></td></tr>
<tr bgcolor="#FFFFFF">
<td width="35" class="lb rb"><input type="checkbox" name="oddsShow" data-id="1" value="0"></td>
<td height="25">澳*</td>
<td><span class='down' companyID='1'></span></td>
<td>0.91</td><td>2.5/3</td><td>0.71</td>
<td>0.90</td><td>2.5/3</td><td>0.72</td>
<td><a href="/changeDetail/handicap.aspx?id=3095429&companyID=1&l=0">指数</a></td>
</tr>
</table></body></html>"""


def test_ah_number():
    assert parse_ah_number("一球") == -1.0
    assert parse_ah_number("一球/球半") == -1.25
    assert parse_ah_number("半球") == -0.5
    assert parse_ah_number("平手") == 0.0
    assert parse_ah_number("受让一球") == 1.0
    assert parse_ah_number("受让平手/半球") == 0.25
    assert parse_ah_number("") is None
    assert parse_ah_number("未知盘口") is None


def test_ou_number():
    assert parse_ou_number("2.5/3") == 2.75
    assert parse_ou_number("2/2.5") == 2.25
    assert parse_ou_number("3") == 3.0
    assert parse_ou_number("") is None


def test_parse_detail():
    info = Titan007Source.parse_detail(DETAIL_HTML, "3095429")
    assert info["status"] == "scheduled"
    assert info["kickoff_at"] == "2026-09-29T14:00:00+08:00"
    assert info["home_team"] == "朝鲜女足"
    assert info["away_team"] == "中国女足"
    assert info["competition"] == "亚运女足"


def test_parse_detail_finished():
    html = DETAIL_HTML.replace("var state = 0;", "var state = -1;")
    info = Titan007Source.parse_detail(html, "3095429")
    assert info["status"] == "finished"


def test_parse_x12js():
    x = Titan007Source.parse_x12js(X12_JS)
    assert x["hometeam_cn"] == "朝鲜女足"
    assert x["guestteam_cn"] == "中国女足"
    assert x["neutrality"] == "1"
    assert x["hometeamID"] == 2697
    assert len(x["companies"]) == 2
    c0 = x["companies"][0]
    assert c0["name"] == "Lottery Official"
    assert c0["open"] == [1.32, 4.25, 7.3]
    assert c0["live"] == [1.36, 4.25, 6.3]
    assert x["history"]["158236391"][0]["home"] == 1.36
    assert x["history"]["158236391"][0]["time"] == "09-29 02:19"


def test_parse_analysis():
    ana = Titan007Source.parse_analysis(ANALYSIS_HTML, 2697, 1933)
    h2h = ana["h2h"]
    assert len(h2h) == 1
    # 主队视角：朝鲜 1-2 中国
    assert h2h[0]["gf"] == 1 and h2h[0]["ga"] == 2
    hr = ana["home_recent"]
    assert len(hr) == 2
    # 第一条：朝鲜(中) 5-1 中国台湾 -> N 场，gf=5
    assert hr[0]["gf"] == 5 and hr[0]["ga"] == 1 and hr[0]["venue"] == "N"
    # 第二条：越南 0-3 朝鲜 -> 客场，gf=3
    assert hr[1]["gf"] == 3 and hr[1]["ga"] == 0 and hr[1]["venue"] == "A"
    ar = ana["away_recent"]
    assert len(ar) == 1
    assert ar[0]["gf"] == 0 and ar[0]["ga"] == 0 and ar[0]["venue"] == "N"


def test_parse_odds_table_asian():
    rows = Titan007Source._parse_odds_table(ASIAN_HTML, "asian")
    assert len(rows) == 2
    assert rows[0]["company"] == "澳*"
    assert rows[0]["open_line"] == "一球"
    assert rows[0]["live_water1"] == "0.76"
    assert rows[1]["live_line"] == "一球/球半"


def test_parse_odds_table_ou():
    rows = Titan007Source._parse_odds_table(OU_HTML, "ou")
    assert len(rows) == 1
    assert rows[0]["open_line"] == "2.5/3"


CORNER_HTML = """<html><body><table id="odds">
<tr bgcolor="#FAFAFA">
<td height="25">36*</td>
<td title="0001-01-01 00:00">0.90</td>
<td title="0001-01-01 00:00" goals="10">10</td>
<td title="0001-01-01 00:00">0.90</td>
<td oddstype="wholeLastOdds">0.90</td>
<td goals="10" oddstype="wholeLastOdds">10</td>
<td oddstype="wholeLastOdds">0.90</td>
<td><a href="/changeDetail/corner.aspx">指数</a></td>
</tr>
</table></body></html>"""


def test_parse_odds_table_corner():
    # 角球表无 checkbox/趋势列：大水/角球数/小水
    rows = Titan007Source._parse_odds_table(CORNER_HTML, "corner")
    assert len(rows) == 1
    assert rows[0]["company"] == "36*"
    assert rows[0]["open_water1"] == "0.90"
    assert rows[0]["open_line"] == "10"
    assert rows[0]["open_water2"] == "0.90"


def test_avg_x12():
    x = Titan007Source.parse_x12js(X12_JS)
    avg = Titan007Source._avg_x12(x["companies"], "live")
    assert avg == {"home": 1.42, "draw": 4.0, "away": 6.15}


def test_member_blacklist_documented():
    assert set(MEMBER_ONLY_MODULES) == {"情报", "AI解读", "会员", "HOT 方案"}
    import collector.collector.sources.titan007 as mod
    src = open(mod.__file__, encoding="utf-8").read()
    # 模块内绝不能出现会员模块的 URL 构造
    assert "aiplus.titan007.com" not in src
    assert "tuijian" not in src


def test_parse_x12js_movement():
    x = Titan007Source.parse_x12js(X12_JS)
    assert x["companies"][0]["rid"] == "158236391"
    mv = x["movement"]
    assert set(mv) == {"Lottery Official", "Bet 365"}
    pts = mv["Lottery Official"]["points"]
    # 时间正序：首点最早
    assert pts[0]["time"] == "09-28 12:00"
    assert pts[-1]["time"] == "09-29 02:19"
    assert pts[0]["home"] == 1.32
    assert pts[-1]["home"] == 1.36
    assert mv["Lottery Official"]["n"] == 2
    assert mv["Lottery Official"]["first_t"] == "09-28 12:00"
    assert mv["Lottery Official"]["last_t"] == "09-29 02:19"
    assert mv["Bet 365"]["n"] == 1


def test_parse_trend_datastr():
    s = "09-27 17:34^0.89^1^1^0.81,09-29 13:59^0.92^1.25^1^0.78"
    pts = Titan007Source.parse_trend_datastr(s, "asian")
    assert pts[0] == {"t": "09-27 17:34", "home_water": 0.89,
                      "handicap": 1.0, "away_water": 0.81}
    assert pts[-1]["handicap"] == 1.25
    ou = Titan007Source.parse_trend_datastr(
        "09-27 17:34^0.91^2.75^1^0.71", "ou")
    assert ou[0] == {"t": "09-27 17:34", "over_water": 0.91,
                     "line": 2.75, "under_water": 0.71}
    assert Titan007Source.parse_trend_datastr("", "asian") is None
    assert Titan007Source.parse_trend_datastr("garbage", "ou") is None
    assert Titan007Source.parse_trend_datastr(s, "corner") is None


def test_trend_company_ids():
    html = """<table>
<tr><td height="25">澳*<img src='x.gif'></td>
<td><span class='down' companyID='1'></span></td></tr>
<tr><td height="25">36*</td>
<td><span class='down' companyID='8'></span></td></tr>
<tr><td height="25">公司</td><td></td></tr></table>"""
    pairs = Titan007Source.trend_company_ids(html)
    assert pairs == [("1", "澳*"), ("8", "36*")]


def test_fetch_company_trend_bad_market_no_network():
    src = Titan007Source.__new__(Titan007Source)
    assert src.fetch_company_trend("1", "1", "nope") is None
