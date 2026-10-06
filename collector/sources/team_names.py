"""
队名规范化 + team_id_map.json 统一查询。

各数据源的队名写法不一致：
- thesportsdb/espn: "Arsenal", "Brighton and Hove Albion"
- transfermarkt/footystats: "Arsenal FC", "Brighton & Hove Albion FC"
- matchbook: "Arsenal", "Brighton" 等简称变体

本模块做规范化（小写、去 FC/AFC 后缀、&→and、去重空格），
再查 data/team_id_map.json。缺失的队按需增补到 JSON，
不在各采集器里写一次性硬编码。
"""

import json
import os
import re

MAP_FILE = os.path.join(os.path.dirname(__file__), "..", "..",
                        "data", "team_id_map.json")

# 队名尾部可忽略的后缀（规范化时剥离）
_SUFFIXES = (" fc", " afc", " cf", " sc")


def normalize(name):
    """规范化队名 -> 小写、无后缀 key。"""
    if not name:
        return ""
    s = name.strip().lower()
    s = s.replace("&", "and")
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_map():
    with open(MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def lookup(name, team_map=None):
    """
    查队名对应的映射条目；找不到返回 None。
    先精确小写匹配，再规范化匹配。
    """
    if team_map is None:
        team_map = load_map()
    key = name.strip().lower()
    if key in team_map:
        return team_map[key]
    nkey = normalize(name)
    if nkey in team_map:
        return team_map[nkey]
    return None


def add_team(key, entry):
    """增补队到 team_id_map.json（key 为规范化后的小写名）。"""
    team_map = load_map()
    nkey = normalize(key)
    if nkey in team_map:
        return False
    team_map[nkey] = entry
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(team_map, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return True


if __name__ == "__main__":
    for n in ["Arsenal FC", "Brighton & Hove Albion FC", "Coventry City",
              "Manchester United FC", "Ipswich Town", "TSC"]:
        e = lookup(n)
        print(f"{n} -> {e['name'] if e else 'NOT FOUND'}")
