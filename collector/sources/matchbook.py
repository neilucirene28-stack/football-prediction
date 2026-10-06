"""
Matchbook Exchange API 接入 — 交易量（volume）数据源
https://api.matchbook.com/edge/rest/

完全免费，无需 key。events 接口返回每场比赛的交易量，
是用户点名要的"当前交易量"信号来源。

覆盖: Matchbook 上有盘的所有足球赛事（含主流+小联赛）
用途: volume_weight 影子特征（只记录不进生产权重，待 walk-forward 验证）

防泄漏：只取 start > 抓取时刻 且 status=open 的未开赛场次。
"""

import time
from datetime import datetime, timezone

from _http import fetch_with_retry

BASE_URL = "https://api.matchbook.com/edge/rest"
SPORT_ID_SOCCER = 15


def _request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    return fetch_with_retry(url, timeout=25)


def _split_name(name):
    """'Home vs Away' -> (home, away)；解析失败返回 (None, None)"""
    if not name or " vs " not in name:
        return None, None
    parts = name.split(" vs ")
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


def get_events(per_page=200, max_pages=5):
    """
    获取足球 events（含交易量）。
    返回: list of {
        event_id, home, away, start (datetime),
        volume (float, 该场总交易量), markets (int),
        status, in_running
    }
    """
    out = []
    offset = 0
    for _ in range(max_pages):
        data = _request("/events", {
            "sport-ids": SPORT_ID_SOCCER,
            "per-page": per_page,
            "offset": offset,
        })
        events = data.get("events", []) or []
        if not events:
            break
        for e in events:
            home, away = _split_name(e.get("name", ""))
            start_raw = e.get("start", "")
            try:
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                start = None
            try:
                volume = float(e.get("volume") or 0)
            except (ValueError, TypeError):
                volume = 0.0
            out.append({
                "event_id": e.get("id"),
                "home": home,
                "away": away,
                "start": start,
                "volume": volume,
                "markets": len(e.get("markets", []) or []),
                "status": e.get("status"),
                "in_running": bool(e.get("in-running-flag")),
                "source": "matchbook",
            })
        total = data.get("total", 0)
        offset += len(events)
        if offset >= total:
            break
        time.sleep(1)  # 礼貌间隔
    return out


def get_upcoming_volumes(asof=None):
    """
    只返回未开赛且 open 的场次（防泄漏）。
    返回同 get_events，但 start 一定 > asof。
    """
    if asof is None:
        asof = datetime.now(timezone.utc)
    out = []
    for e in get_events():
        if e["start"] is None or e["start"] <= asof:
            continue
        if e["status"] != "open" or e["in_running"]:
            continue
        if not e["home"] or not e["away"]:
            continue
        out.append(e)
    return out


def volume_weight(volume, median_volume):
    """
    影子特征：交易量权重（纯函数，可离线测试）。
    volume 相对于当日中位数的对数权重，上限 3.0，下限 0.2。
    只用于记录，不进生产。
    """
    import math
    if not median_volume or median_volume <= 0 or volume <= 0:
        return 1.0
    w = 1.0 + math.log(volume / median_volume)
    return max(0.2, min(3.0, w))


if __name__ == "__main__":
    print("=== Matchbook 交易量测试 ===")
    evs = get_upcoming_volumes()
    print(f"未开赛 open 场次: {len(evs)}")
    vols = sorted([e["volume"] for e in evs], reverse=True)
    med = vols[len(vols) // 2] if vols else 0
    print(f"交易量中位数: {med:.0f}")
    for e in evs[:8]:
        st = e["start"].strftime("%m-%d %H:%M")
        w = volume_weight(e["volume"], med)
        print(f"  {st} {e['home']} vs {e['away']}: vol={e['volume']:.0f} w={w:.2f}")
