"""
premierinjuries.com 解析测试：用内联 HTML fixture 测 parse_injury_table，不真调网络。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector", "sources"))

import pytest

import premierinjuries as pi


FIXTURE = """
<table class="injury-table injury-table-full injury-table-epl">
<tr class="heading" data-team-id="119">
<th colspan="7"><div class="injury-table-th">
<div class="injury-team">AFC Bournemouth</div>
</div></th></tr>
<tr class="sub-head team_119">
<td>Player</td><td>Reason</td><td>Further Detail</td><td>Potential Return</td>
<td>Condition</td><td>Status</td><td></td></tr>
<tr class="player-row team_119">
<td><div class="mob-title">Player</div>Alex Scott</td>
<td><div class="mob-title">Reason</div>Thigh Injury</td>
<td><div class="mob-title">Further Detail</div>Oct 08: 'We're going to miss him for four or five weeks.'<br/><a href="/newsroom/epl/players/alex-scott">See Player Page</a></td>
<td><div class="mob-title">Potential Return</div>21/11/2026</td>
<td><div class="mob-title">Condition</div>Currently Being Assessed</td>
<td><div class="mob-title">Status</div>Ruled Out</td>
<td></td></tr>
<tr class="player-row team_119">
<td><div class="mob-title">Player</div>David Brooks</td>
<td><div class="mob-title">Reason</div>Other</td>
<td><div class="mob-title">Further Detail</div>Muscular Injury</td>
<td><div class="mob-title">Potential Return</div>10/10/2026</td>
<td><div class="mob-title">Condition</div>Passed Fit</td>
<td><div class="mob-title">Status</div>100%</td>
<td></td></tr>
<tr class="heading" data-team-id="43">
<th colspan="7"><div class="injury-table-th">
<div class="injury-team">Brighton &amp; Hove Albion</div>
</div></th></tr>
<tr class="sub-head team_43">
<td>Player</td><td>Reason</td><td>Further Detail</td><td>Potential Return</td>
<td>Condition</td><td>Status</td><td></td></tr>
<tr class="player-row team_43">
<td><div class="mob-title">Player</div>Taiwo Awoniyi</td>
<td><div class="mob-title">Reason</div>Suspended</td>
<td><div class="mob-title">Further Detail</div>Sending Off - Red Card</td>
<td><div class="mob-title">Potential Return</div>19/10/2026</td>
<td><div class="mob-title">Condition</div>Not Available</td>
<td><div class="mob-title">Status</div>Ruled Out</td>
<td></td></tr>
<tr class="player-row team_43">
<td><div class="mob-title">Player</div>Marco Bizot</td>
<td><div class="mob-title">Reason</div>Lower Back Injury</td>
<td><div class="mob-title">Further Detail</div>Sept 16: 'Marco felt pain in his back.'</td>
<td><div class="mob-title">Potential Return</div>10/10/2026</td>
<td><div class="mob-title">Condition</div>Not Available</td>
<td><div class="mob-title">Status</div>Ruled Out</td>
<td></td></tr>
</table>
"""


def test_injury_row_fields():
    rows = pi.parse_injury_table(FIXTURE)
    scott = next(r for r in rows if r["player"] == "Alex Scott")
    assert scott["team"] == "AFC Bournemouth"
    assert scott["injury"] == "Thigh Injury"
    assert scott["status"] == "受伤"
    assert scott["status_raw"] == "Ruled Out"
    assert scott["condition"] == "Currently Being Assessed"
    assert scott["expected_return"] == "21/11/2026"
    assert scott["expected_return_iso"] == "2026-11-21"
    assert scott["updated"] == "2026-10-08"
    assert "See Player Page" not in scott["detail"]
    assert scott["detail"].startswith("Oct 08:")


def test_suspended_maps_to_ting_sai():
    rows = pi.parse_injury_table(FIXTURE)
    awoniyi = next(r for r in rows if r["player"] == "Taiwo Awoniyi")
    assert awoniyi["injury"] == "Suspended"
    assert awoniyi["status"] == "停赛"
    assert awoniyi["detail"] == "Sending Off - Red Card"
    assert awoniyi["updated"] is None  # 无日期前缀的 detail 不产出 updated


def test_passed_fit_maps_to_fu_chu():
    rows = pi.parse_injury_table(FIXTURE)
    brooks = next(r for r in rows if r["player"] == "David Brooks")
    assert brooks["status"] == "复出"
    assert brooks["expected_return_iso"] == "2026-10-10"


def test_team_switch_and_entity_unescape():
    rows = pi.parse_injury_table(FIXTURE)
    assert len(rows) == 4
    teams = {r["team"] for r in rows}
    assert teams == {"AFC Bournemouth", "Brighton & Hove Albion"}  # &amp; 解码
    bizot = next(r for r in rows if r["player"] == "Marco Bizot")
    assert bizot["team"] == "Brighton & Hove Albion"
    assert bizot["updated"] == "2026-09-16"


def test_missing_table_raises():
    with pytest.raises(ValueError, match="找不到 injury-table-full"):
        pi.parse_injury_table("<html><body>no table here</body></html>")


def test_empty_table_raises():
    empty = ('<table class="injury-table injury-table-full injury-table-epl">'
             "<tr><td>nothing</td></tr></table>")
    with pytest.raises(ValueError, match="0 条记录"):
        pi.parse_injury_table(empty)
