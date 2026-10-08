"""7M find_mid 回归测试：港式译名别名 + 别名/客队关键词补搜（2026-10-08）。

背景：7M 用港式音译（山度士/法林明高/彭美拉斯…），且按关键词搜当日比赛；
主队名在 7M 无结果时（如巴竞技→帕拉尼恩斯）需用别名或客队名补搜，
否则整节 0 映射。全部用 mock，不碰网络。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector", "sources"))

import qim

# 模拟 7M 搜索返回（繁体+港式，与实测一致）
SEARCH_DB = {
    "桑托斯": [],
    "山度士": [{"mid": "5006485", "home": "山度士", "away": "法林明高",
                "score": "", "state": "17"}],
    "弗拉门戈": [{"mid": "5006485", "home": "山度士", "away": "法林明高",
                  "score": "", "state": "17"}],
    "巴竞技": [],
    "米竞技": [],
    "帕拉尼恩斯": [{"mid": "5006477", "home": "帕拉尼恩斯", "away": "明尼路",
                    "score": "", "state": "17"}],
    "明尼路": [{"mid": "5006477", "home": "帕拉尼恩斯", "away": "明尼路",
                "score": "", "state": "17"}],
    "帕梅拉斯": [],
    "巴伊亚": [{"mid": "5006483", "home": "彭美拉斯", "away": "巴希亞",
                "score": "", "state": "17"}],
    "彭美拉斯": [{"mid": "5006483", "home": "彭美拉斯", "away": "巴希亞",
                  "score": "", "state": "17"}],
}


def _fake_search(keyword):
    return SEARCH_DB.get(keyword, [])


def test_find_mid_hk_alias():
    orig = qim.search_match
    qim.search_match = _fake_search
    try:
        assert qim.find_mid("桑托斯", "弗拉门戈") == "5006485"
    finally:
        qim.search_match = orig


def test_find_mid_alias_keyword_fallback():
    # 主客队名在 7M 均无结果，靠别名关键词（帕拉尼恩斯/明尼路）补搜命中
    orig = qim.search_match
    qim.search_match = _fake_search
    try:
        assert qim.find_mid("巴竞技", "米竞技") == "5006477"
    finally:
        qim.search_match = orig


def test_find_mid_away_keyword_fallback():
    # 主队名无结果，客队名（巴伊亚）搜到；巴希亞→巴伊亚别名参与匹配
    orig = qim.search_match
    qim.search_match = _fake_search
    try:
        assert qim.find_mid("帕梅拉斯", "巴伊亚") == "5006483"
    finally:
        qim.search_match = orig


def test_find_mid_no_match():
    orig = qim.search_match
    qim.search_match = _fake_search
    try:
        assert qim.find_mid("不存在", "也不存在") is None
    finally:
        qim.search_match = orig
