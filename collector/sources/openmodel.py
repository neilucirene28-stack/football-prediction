"""
The Open Model (Hicruben) 预测数据接入
https://theopenmodel.com/data/

Elo + Dixon-Coles + Monte Carlo，walk-forward 回测公开，
CC BY 4.0 协议免费使用。

覆盖: 英超、西甲、意甲、德甲、法甲 (2026-27赛季)
用途: 第4个独立信号 / 分歧检测 (与市场赔率对照)

数据每天更新，predictions.csv 含未来赛程的预测概率
"""

import csv
import os
import time
from datetime import datetime, timezone

from _http import download_with_retry

BASE_URL = "https://theopenmodel.com/data"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "beidan-mvp", "data", "openmodel")
CACHE_DIR = os.path.abspath(CACHE_DIR)

# 联赛代码映射: openmodel -> 咱们内部
LEAGUE_MAP = {
    "premier-league": "英超",
    "la-liga": "西甲",
    "serie-a": "意甲",
    "bundesliga": "德甲",
    "ligue-1": "法甲",
}

# 缓存有效期 (小时)
CACHE_TTL_HOURS = 12


def _download(filename):
    """下载 CSV 到缓存目录（带重试/退避）"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    url = f"{BASE_URL}/{filename}"
    dest = os.path.join(CACHE_DIR, filename)
    download_with_retry(url, dest, timeout=30, max_retries=3, min_size=100)
    return dest


def _is_cache_fresh(filename):
    path = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(path):
        return False
    age_hours = (time.time() - os.path.getmtime(path)) / 3600
    return age_hours < CACHE_TTL_HOURS


def filter_upcoming(predictions, asof=None):
    """
    只保留 kickoff 在 asof 之后的预测。

    防泄漏：不能只凭 result 为空判断未开赛 —— 源文件里存在
    kickoff 已过但 result 为空的陈旧行，必须按时间过滤。
    纯函数，可离线测试。
    """
    if asof is None:
        asof = datetime.now(timezone.utc)
    out = []
    for p in predictions:
        ko = p.get("kickoff")
        if ko is None:
            continue
        if ko > asof:
            out.append(p)
    return out


def snapshot_is_stale(predictions, asof=None, max_age_days=3):
    """
    预测快照是否过期：
    - 空列表 → True
    - 最新一场的 kickoff 距 asof 超过 max_age_days 天 → True
    （说明源文件超过 max_age_days 天没有新增未来场次）
    纯函数，可离线测试。
    """
    if not predictions:
        return True
    if asof is None:
        asof = datetime.now(timezone.utc)
    kickoffs = [p["kickoff"] for p in predictions if p.get("kickoff")]
    if not kickoffs:
        return True
    newest = max(kickoffs)
    return (asof - newest).total_seconds() > max_age_days * 86400


def _parse_unsettled():
    """解析 CSV 中所有 result 为空的行（不过滤开球时间）。内部用。"""
    filename = "predictions.csv"
    if not _is_cache_fresh(filename):
        _download(filename)

    path = os.path.join(CACHE_DIR, filename)
    out = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 跳过已完赛的 (有 result 的)
            if row.get("result", "").strip():
                continue
            try:
                kickoff = datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue
            try:
                p_h = float(row["pHome"])
                p_d = float(row["pDraw"])
                p_a = float(row["pAway"])
            except (ValueError, KeyError):
                continue
            out.append({
                "kickoff": kickoff,
                "league": LEAGUE_MAP.get(row.get("league", ""), row.get("league", "")),
                "league_code": row.get("league", ""),
                "home": row.get("home", "").strip(),
                "away": row.get("away", "").strip(),
                "p_home": p_h,
                "p_draw": p_d,
                "p_away": p_a,
                "model_pick": row.get("modelPick", "").strip(),
                "source": "theopenmodel",
            })
    return out


def get_predictions(force_refresh=False, only_upcoming=True):
    """
    获取 The Open Model 的全部预测
    返回: list of {
        kickoff (datetime), league, home, away,
        p_home, p_draw, p_away, model_pick
    }
    only_upcoming=True（默认）: 只返回未开赛的 (result 为空 且 kickoff 在未来)，
        防泄漏 —— 不能只凭 result 为空判断未开赛；
    only_upcoming=False: 返回全部未结算行，供调用方做新鲜度检查后自行过滤。
    """
    if force_refresh:
        _download("predictions.csv")
    out = _parse_unsettled()
    if only_upcoming:
        out = filter_upcoming(out)
    return out


def find_match(predictions, home, away, kickoff_date=None):
    """
    在预测列表里找某场比赛 (模糊匹配队名)
    home/away: 咱们内部的队名
    kickoff_date: datetime.date 或 None (不过滤日期)
    返回匹配的预测 dict 或 None
    """
    def norm(s):
        return s.lower().replace(" ", "").replace("-", "").replace(".", "")

    nh, na = norm(home), norm(away)
    for p in predictions:
        if norm(p["home"]) == nh and norm(p["away"]) == na:
            if kickoff_date and p["kickoff"].date() != kickoff_date:
                continue
            return p
        # 兼容主客场写反的情况 (极少)
    return None


def get_track_record():
    """
    获取他们的历史战绩 (已结算的预测)
    返回: {total, correct, accuracy}
    """
    filename = "predictions.csv"
    if not _is_cache_fresh(filename):
        _download(filename)

    path = os.path.join(CACHE_DIR, filename)
    total, correct = 0, 0
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("result", "").strip():
                continue
            total += 1
            if row.get("correct", "").strip().lower() == "true":
                correct += 1
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0,
        "source": "theopenmodel.com/record",
    }


if __name__ == "__main__":
    print("=== The Open Model 接入测试 ===")
    preds = get_predictions(force_refresh=True)
    print(f"未开赛预测: {len(preds)} 场")

    # 按联赛统计
    from collections import Counter
    by_league = Counter(p["league"] for p in preds)
    for lg, cnt in by_league.most_common():
        print(f"  {lg}: {cnt} 场")

    # 最近几场
    preds_sorted = sorted(preds, key=lambda x: x["kickoff"])
    print("\n最近5场:")
    for p in preds_sorted[:5]:
        ko = p["kickoff"].strftime("%m-%d %H:%M")
        print(f"  {ko} {p['league']} {p['home']} vs {p['away']}")
        print(f"    胜{p['p_home']:.1%} 平{p['p_draw']:.1%} 负{p['p_away']:.1%} → {p['model_pick']}")

    print("\n--- 历史战绩 ---")
    rec = get_track_record()
    print(f"  {rec['correct']}/{rec['total']} = {rec['accuracy']:.1%}")
    print("\n数据来源: theopenmodel.com (CC BY 4.0)")
