"""无 DB 时的演示比赛数据（与 collector DemoSource 同源的静态版本）。"""
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _mk(pattern, venue, n=8):
    seq = pattern * ((n // len(pattern)) + 1)
    return [{"gf": gf, "ga": ga, "venue": venue} for gf, ga in seq[:n]]


STRONG_HOME = [(2, 1), (1, 0), (3, 1), (2, 0), (1, 1), (2, 2), (1, 0), (0, 1)]
MID_AWAY = [(1, 1), (0, 2), (1, 0), (2, 2), (0, 1), (1, 2), (2, 1), (1, 1)]
WEAK_AWAY = [(0, 2), (1, 3), (0, 1), (1, 1), (0, 0), (1, 2), (2, 3), (0, 1)]


def demo_matches() -> list[dict]:
    now = datetime.now(TZ)
    samples = [
        ("欧国联", "土耳其", "意大利",
         {"home": 2.69, "draw": 3.30, "away": 2.20}, _mk(STRONG_HOME, "H"), _mk(MID_AWAY, "A")),
        ("欧国联", "比利时", "法国",
         {"home": 3.77, "draw": 3.77, "away": 1.67}, _mk(STRONG_HOME, "H"), _mk(MID_AWAY, "A")),
        ("欧国联", "瑞典", "波兰",
         {"home": 1.69, "draw": 3.70, "away": 3.74}, _mk(STRONG_HOME, "H"), _mk(WEAK_AWAY, "A")),
    ]
    out = []
    for i, (comp, h, a, odds, hr, ar) in enumerate(samples):
        ko = now + timedelta(days=1, hours=i * 3)
        out.append({
            "id": 1001 + i, "source": "demo", "competition": comp,
            "home_team": h, "away_team": a,
            "kickoff_at": ko.isoformat(), "status": "scheduled",
            "odds": odds, "home_recent": hr, "away_recent": ar,
            "snapshot_at": now.isoformat(),
        })
    return out
