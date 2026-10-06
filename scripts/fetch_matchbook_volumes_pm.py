"""Matchbook 交易所交易量抓取 — 竞彩下午重跑第1路数据源。
匹配 7 场欧国联（北京时间 2026-10-06 00:00/02:45 开球）。
输出 data/daily/2026-10-05/matchbook_volumes_pm.json
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector", "sources"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

from matchbook import get_upcoming_volumes
from team_names import normalize

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data",
                   "daily", "2026-10-05", "matchbook_volumes_pm.json"))
os.makedirs(os.path.dirname(OUT), exist_ok=True)

errors = []

# 目标 7 场：(no, 中文主, 中文客, 别名集合)
TARGETS = [
    ("周一001", "塞浦路斯", "拉脱维亚",
     {"cyprus"}, {"latvia"}),
    ("周一002", "法国", "比利时",
     {"france"}, {"belgium"}),
    ("周一003", "罗马尼亚", "瑞典",
     {"romania"}, {"sweden"}),
    ("周一004", "意大利", "土耳其",
     {"italy"}, {"turkey", "turkiye", "türkiye"}),
    ("周一005", "北爱尔兰", "格鲁吉亚",
     {"northern ireland", "n ireland"}, {"georgia"}),
    ("周一006", "黑山", "亚美尼亚",
     {"montenegro"}, {"armenia"}),
    ("周一007", "波黑", "波兰",
     {"bosnia and herzegovina", "bosnia", "bosnia-herzegovina", "bosnia herzegovina"},
     {"poland"}),
]

BJ = timezone(timedelta(hours=8))
captured_at = datetime.now(BJ).isoformat(timespec="seconds")

def name_variants(s):
    """生成名字的候选匹配 key：原始 + 规范化"""
    return {s.strip().lower(), normalize(s)}

def hit(target_aliases, name):
    keys = name_variants(name or "")
    return bool(keys & set(target_aliases))

try:
    events = get_upcoming_volumes()
except Exception:
    errors.append("源站 get_upcoming_volumes 失败: " + traceback.format_exc(limit=3))
    events = []

if not events and not errors:
    errors.append("返回 0 场未开赛场次（源站可能为空或被墙）")

# 记录抓到的全部 events（调试用，可选输出摘要）
found_summary = []
matches = []
for no, zh_home, zh_away, h_alias, a_alias in TARGETS:
    cands = [e for e in events
             if hit(h_alias, e.get("home")) and hit(a_alias, e.get("away"))]
    rec = {"no": no, "home": zh_home, "away": zh_away,
           "volume": None, "matched_name": None, "found": False}
    if cands:
        e = cands[0]
        vol = e.get("volume")
        rec["found"] = True
        rec["matched_name"] = f"{e.get('home')} vs {e.get('away')}"
        try:
            rec["volume"] = float(vol) if vol is not None else None
        except (ValueError, TypeError):
            errors.append(f"{no} volume 字段无法解析: {vol!r}")
        if len(cands) > 1:
            errors.append(f"{no} 有 {len(cands)} 个候选，取了第一个 "
                          f"({rec['matched_name']}, start={e.get('start')})")
        found_summary.append((no, rec["matched_name"], rec["volume"],
                              str(e.get("start"))))
    else:
        errors.append(f"{no} {zh_home} vs {zh_away} 未在返回场次中找到")
    matches.append(rec)

payload = {"source": "matchbook", "captured_at": captured_at,
           "matches": matches, "errors": errors,
           "events_total": len(events)}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(f"events_total={len(events)}")
for s in found_summary:
    print("  FOUND", s[0], s[1], "volume=", s[2], "start=", s[3])
for m in matches:
    if not m["found"]:
        print("  MISS ", m["no"], m["home"], "vs", m["away"])
print("errors:", len(errors))
print("wrote", OUT)
