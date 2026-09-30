"""演示数据源：内置样例比赛，离线可跑通全链路。

数据为示意性质（标注 source=demo），用于演示与测试，
不代表真实赔率或真实球队状态。
"""
from datetime import datetime, timedelta, timezone

from .base import Source

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _mk(pattern, venue, n=8):
    seq = pattern * ((n // len(pattern)) + 1)
    return [{"gf": gf, "ga": ga, "venue": venue} for gf, ga in seq[:n]]


STRONG_HOME = [(2, 1), (1, 0), (3, 1), (2, 0), (1, 1), (2, 2), (1, 0), (0, 1)]
MID_AWAY = [(1, 1), (0, 2), (1, 0), (2, 2), (0, 1), (1, 2), (2, 1), (1, 1)]
WEAK_AWAY = [(0, 2), (1, 3), (0, 1), (1, 1), (0, 0), (1, 2), (2, 3), (0, 1)]


SAMPLES = [
    {
        "external_id": "demo-1001", "competition": "欧国联",
        "home_team": "土耳其", "away_team": "意大利",
        "odds": {"home": 2.69, "draw": 3.30, "away": 2.20},
        "home_recent": _mk(STRONG_HOME, "H"), "away_recent": _mk(MID_AWAY, "A"),
    },
    {
        "external_id": "demo-1002", "competition": "欧国联",
        "home_team": "比利时", "away_team": "法国",
        "odds": {"home": 3.77, "draw": 3.77, "away": 1.67},
        "home_recent": _mk(STRONG_HOME, "H"), "away_recent": _mk(MID_AWAY, "A"),
    },
    {
        "external_id": "demo-1003", "competition": "欧国联",
        "home_team": "瑞典", "away_team": "波兰",
        "odds": {"home": 1.69, "draw": 3.70, "away": 3.74},
        "home_recent": _mk(STRONG_HOME, "H"), "away_recent": _mk(WEAK_AWAY, "A"),
    },
]


class DemoSource(Source):
    name = "demo"

    def fetch_matches(self) -> list[dict]:
        now = datetime.now(TZ)
        out = []
        for i, s in enumerate(SAMPLES):
            m = dict(s)
            m["kickoff_at"] = (now + timedelta(days=1, hours=i * 3)).isoformat()
            m["status"] = "scheduled"
            out.append(m)
        return self.validate(out)
