"""澳客对阵页解析回归测试：联赛名不得被当成队名（2026-10-08实测bug）。

bug：get_board_map 用 title 黑名单过滤联赛名，只写了欧国联/欧罗巴/世界杯/亚洲杯，
芬超/巴西甲等漏网，生成 ("芬超","赫尔火花") 这类脏键，导致 7M 映射连续3天 0 场。
修复：改从 .zhum 的 title 按结构取 (主队, 客队)，不再依赖黑名单。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector", "sources"))

import okooo

FIXTURE = """
<div data-mid="1347927" data-morder="3001">
  <div class="liansai">
    <span class="xulie" title="周三001">001</span>
    <a class="saiming" title="芬超">芬超</a>
    <div class="shijian" title="比赛时间:2026-10-08 00:00:00">22:00</div>
  </div>
  <div class="shenpf">
    <div class="zhu"><div class="zhum fff hui_colo" title="赫尔火花">赫尔火花</div></div>
    <div class="zhum fff hui_colo" title="国际图尔">国际图尔</div>
  </div>
</div>
<div data-mid="1316985" data-morder="3002">
  <div class="liansai">
    <span class="xulie" title="周三002">002</span>
    <a class="saiming" title="巴西甲">巴西甲</a>
    <div class="shijian" title="比赛时间:2026-10-08 06:30:00">06:30</div>
  </div>
  <div class="shenpf">
    <div class="zhu"><div class="zhum fff hui_colo" title="巴西国际">巴西国际</div></div>
    <div class="zhum fff hui_colo" title="科林蒂安">科林蒂安</div>
  </div>
</div>
"""


def _fake_get_html(url, retries=3):
    return FIXTURE


def test_board_map_excludes_league_names():
    orig = okooo._get_html
    okooo._get_html = _fake_get_html
    try:
        m = okooo.get_board_map("jingcai")
    finally:
        okooo._get_html = orig
    assert m == {
        ("赫尔火花", "国际图尔"): "1347927",
        ("巴西国际", "科林蒂安"): "1316985",
    }, m


# 过渡态行：联赛头混入 zhum（2026-10-08 08:15 实测曾出现 ("芬超","赫尔火花")）
FIXTURE_TRANSITIONAL = """
<div data-mid="1347927" data-morder="3001">
  <div class="liansai">
    <span class="xulie" title="周三001">001</span>
    <a class="saiming" title="芬超">芬超</a>
    <div class="zhum fff hui_colo" title="芬超">芬超</div>
  </div>
  <div class="shenpf">
    <div class="zhu"><div class="zhum fff hui_colo" title="赫尔火花">赫尔火花</div></div>
    <div class="zhum fff hui_colo" title="国际图尔">国际图尔</div>
  </div>
</div>
"""


def test_board_map_transitional_league_in_zhum():
    orig = okooo._get_html
    okooo._get_html = lambda url, retries=3: FIXTURE_TRANSITIONAL
    try:
        m = okooo.get_board_map("jingcai")
    finally:
        okooo._get_html = orig
    assert m == {("赫尔火花", "国际图尔"): "1347927"}, m


def test_board_map_no_league_contamination():
    orig = okooo._get_html
    okooo._get_html = _fake_get_html
    try:
        m = okooo.get_board_map("jingcai")
    finally:
        okooo._get_html = orig
    for home, away in m:
        assert "芬超" not in (home, away) and "巴西甲" not in (home, away), (home, away)
