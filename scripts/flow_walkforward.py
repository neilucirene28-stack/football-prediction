#!/usr/bin/env python3
"""资金流向 walk-forward 评估（阶段 2）。

对比三档（只用开球前数据；赛中/赛后数据一律视为泄漏源排除）：
  T1 模型                  p_model
  T2 模型+市场             静态权重融合（生产口径）
  T3 模型+市场+流动性加权  market 权重经 volume_weight 调节后重归一

指标：Brier / LogLoss / RPS / ECE（1x2）。
样本 < 100 → 只看不调，诚实报数（含负结果）。

数据源：
  --db              predictions ⋈ settlements（需 DATABASE_URL）
  --titan-backtest  抓 Titan007 已完赛场次，引擎重跑（asof 防泄漏）。
                    Polymarket 成交量无法回溯 → 历史回测中一律视为缺失，
                    流动性代理降级为 Titan007 公司数量（报告中明确标注）。
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.fusion import ensemble  # noqa: E402
from engine.market_flow import (apply_volume_weight, flow_features,  # noqa: E402
                                liquidity_weight)
from engine.jingcai_predictor import PredictError, predict  # noqa: E402
from collector.collector.sources.titan007 import Titan007Source  # noqa: E402

try:
    import psycopg  # noqa: E402
except ImportError:
    psycopg = None

_TZ8 = timezone(timedelta(hours=8))


# ------------------------------------------------------------------ 指标 --
def brier(p, outcome: int) -> float:
    return sum((p[i] - (1.0 if i == outcome else 0.0)) ** 2 for i in range(3))


def logloss(p, outcome: int) -> float:
    return -math.log(max(p[outcome], 1e-12))


def rps(p, outcome: int) -> float:
    """Ranked Probability Score，1x2 按 home>draw>away 有序。"""
    s, cp, co = 0.0, 0.0, 0.0
    for j in range(2):
        cp += p[j]
        co += 1.0 if outcome == j else 0.0
        s += (cp - co) ** 2
    return s / 2.0


def ece(pairs, n_bins: int = 10) -> float:
    """pairs: [(confidence=max prob, correct 0/1)]。"""
    bins = [[] for _ in range(n_bins)]
    for conf, correct in pairs:
        bins[min(int(conf * n_bins), n_bins - 1)].append((conf, correct))
    n = len(pairs)
    if n == 0:
        return float("nan")
    e = 0.0
    for b in bins:
        if not b:
            continue
        acc = sum(c for _, c in b) / len(b)
        cf = sum(c for c, _ in b) / len(b)
        e += len(b) / n * abs(acc - cf)
    return e


def _as_tuple(x):
    return (float(x[0]), float(x[1]), float(x[2]))


def tiers_from_result(result: dict, volume_weight: float):
    """T1/T2/T3。signals 缺 market 时 T2=T3=T1（退化，照实记录）。"""
    sig = result.get("signals") or {}
    signals = {
        "model": _as_tuple(sig["model"]) if sig.get("model") else None,
        "market": _as_tuple(sig["market"]) if sig.get("market") else None,
        "elo": _as_tuple(sig["elo"]) if sig.get("elo") else None,
    }
    weights = {k: float(v) for k, v in (result.get("weights") or {}).items()}
    t1 = signals["model"]
    t2 = ensemble(signals, weights) if t1 else None
    w3 = apply_volume_weight(weights, volume_weight)
    t3 = ensemble(signals, w3) if t1 else None
    return t1, t2, t3


# ------------------------------------------------------------------ 评估 --
class Eval:
    def __init__(self):
        self.rows = []  # (t1, t2, t3, outcome)

    def add(self, t1, t2, t3, outcome: int):
        self.rows.append((t1, t2, t3, outcome))

    def report(self) -> dict:
        n = len(self.rows)
        out = {"n": n, "tiers": {}}
        for ti, name in enumerate(("T1_模型", "T2_模型+市场", "T3_模型+市场+流动性")):
            ps = [r[ti] for r in self.rows if r[ti] is not None]
            oc = [r[3] for r in self.rows if r[ti] is not None]
            if not ps:
                out["tiers"][name] = {"n": 0}
                continue
            out["tiers"][name] = {
                "n": len(ps),
                "brier": round(sum(brier(p, o) for p, o in zip(ps, oc))
                               / len(ps), 4),
                "logloss": round(sum(logloss(p, o) for p, o in zip(ps, oc))
                                 / len(ps), 4),
                "rps": round(sum(rps(p, o) for p, o in zip(ps, oc))
                             / len(ps), 4),
                "ece": round(ece([(max(p), int(max(range(3),
                               key=lambda i: p[i]) == o))
                                   for p, o in zip(ps, oc)]), 4),
                "acc": round(sum(1 for p, o in zip(ps, oc)
                                 if max(range(3), key=lambda i: p[i]) == o)
                             / len(ps), 4),
            }
        return out


def _print_report(rep: dict, diag: dict):
    print("=" * 64)
    print("资金流向 walk-forward 评估")
    print("=" * 64)
    print(f"样本 n = {rep['n']}")
    if rep["n"] < 100:
        print("⚠️  样本 < 100：只看不调，不做任何参数调整")
    print(f"{'tier':<22}{'n':>5}{'brier':>8}{'logloss':>9}"
          f"{'rps':>8}{'ece':>8}{'acc':>8}")
    for name, m in rep["tiers"].items():
        if not m.get("n"):
            print(f"{name:<22}{'0':>5}  --")
            continue
        print(f"{name:<22}{m['n']:>5}{m['brier']:>8.4f}{m['logloss']:>9.4f}"
              f"{m['rps']:>8.4f}{m['ece']:>8.4f}{m['acc']:>8.4f}")
    print("-" * 64)
    print("诊断（只用开球前数据）：")
    for k, v in diag.items():
        print(f"  {k}: {v}")
    t2, t3 = rep["tiers"].get("T2_模型+市场", {}), \
        rep["tiers"].get("T3_模型+市场+流动性", {})
    if t2.get("n") and t3.get("n"):
        d = (t3["brier"] - t2["brier"])
        print(f"ΔBrier(T3-T2) = {d:+.4f}  "
              f"({'流动性加权更优' if d < 0 else '流动性加权更差或持平'})")


# ------------------------------------------------------------ DB 模式 --
def _j(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            return {}
    return x or {}


def eval_db(db: str, limit: int = 5000):
    ev, diag_n = Eval(), {"pm_fallback_company_count": 0}
    drifts, divs, vws, cover = [], [], [], {}
    with psycopg.connect(db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.signals, p.weights, p.payload,
                          s.home_goals, s.away_goals
                   FROM predictions p
                   JOIN settlements s ON s.prediction_id = p.prediction_id
                   ORDER BY p.kickoff_at ASC LIMIT %s""", (limit,))
            rows = cur.fetchall()
    for signals, weights, payload, hg, ag in rows:
        signals, weights, payload = _j(signals), _j(weights), _j(payload)
        if hg is None or ag is None or not signals.get("model"):
            continue
        outcome = 0 if hg > ag else (1 if hg == ag else 2)
        raw = (payload.get("input") or {}).get("raw") or {}
        companies = raw.get("x12_companies") or {}
        feat = flow_features(companies)
        # Polymarket 成交量无法回溯 → 一律视为缺失，降级用公司数量
        liq = liquidity_weight(n_companies=feat["n_companies"])
        diag_n["pm_fallback_company_count"] += 1
        result = {"signals": signals, "weights": weights}
        t1, t2, t3 = tiers_from_result(result, liq["volume_weight"])
        ev.add(t1, t2, t3, outcome)
        if feat["drift_avg"]:
            drifts.append(abs(feat["drift_avg"]["home"]))
        if feat["sharp_divergence"] is not None:
            divs.append(feat["sharp_divergence"])
        vws.append(liq["volume_weight"])
        cover[feat["drift_coverage"]] = cover.get(feat["drift_coverage"], 0) + 1
    diag = {
        "polymarket_回测视为缺失": diag_n["pm_fallback_company_count"],
        "drift_覆盖": cover,
        "平均|drift_home|": round(sum(drifts) / len(drifts), 4) if drifts else None,
        "平均sharp_divergence": round(sum(divs) / len(divs), 4) if divs else None,
        "平均volume_weight": round(sum(vws) / len(vws), 4) if vws else None,
    }
    return ev.report(), diag


# ------------------------------------------------------ Titan 回测模式 --
_SCORE_RE = re.compile(r"比赛结束！\s*比分：(\d+)-(\d+)")


def _filt_rows(rows, kickoff_day: str):
    # 严格 walk-forward：只用开球日"之前"的比赛，排除同日（可能含本场或
    # 同日已赛场次，造成数据泄漏）。保守但安全。
    return [r for r in (rows or [])
            if isinstance(r, dict) and r.get("date") and
            r["date"] < kickoff_day and isinstance(r.get("gf"), int)]


def _eval_one_titan(mid, src):
    """单场 Titan 回测评估（可能 hang 在网络 I/O，调用方用线程超时隔离）。

    返回 (status, data)：
      'skip'   -> 数据/解析问题，该场跳过
      'nodata' -> 公司数据或引擎数据不足
      'error'  -> 引擎异常
      'ok'     -> data = dict(t1,t2,t3,outcome,drift_home,div,vw,coverage)
    """
    try:
        html = src._get(
            f"https://live.titan007.com/detail/{mid}cn.htm")
    except Exception:
        return "skip", None
    info = Titan007Source.parse_detail(html, mid)
    if not info or info.get("status") != "finished":
        return "skip", None
    mscore = _SCORE_RE.search(html)
    if not mscore:
        return "skip", None
    hg, ag = int(mscore.group(1)), int(mscore.group(2))
    try:
        m = src._build_match(info)
    except Exception:
        return "skip", None
    companies = (m.get("raw") or {}).get("x12_companies") or {}
    if not companies or not m.get("odds"):
        return "nodata", None
    ko = datetime.fromisoformat(info["kickoff_at"])
    asof = ko - timedelta(hours=1)
    kday = ko.strftime("%Y-%m-%d")
    payload = {
        "home": m["home_team"], "away": m["away_team"],
        "kickoff_at": info["kickoff_at"],
        "snapshot_at": asof.isoformat(),
        "odds": m["odds"], "opening_odds": m.get("opening_odds"),
        "home_recent": _filt_rows(m.get("home_recent"), kday),
        "away_recent": _filt_rows(m.get("away_recent"), kday),
        "h2h": _filt_rows(m.get("h2h"), kday),
    }
    if m.get("neutral_site"):
        payload["neutral_site"] = True
    try:
        result = predict(payload, asof=asof)
    except (PredictError, Exception):
        return "error", None
    if result.get("status") == "insufficient_data":
        return "nodata", None
    outcome = 0 if hg > ag else (1 if hg == ag else 2)
    feat = flow_features(companies)
    liq = liquidity_weight(n_companies=feat["n_companies"])
    t1, t2, t3 = tiers_from_result(result, liq["volume_weight"])
    return "ok", {
        "t1": t1, "t2": t2, "t3": t3, "outcome": outcome,
        "drift_home": abs(feat["drift_avg"]["home"])
        if feat["drift_avg"] else None,
        "div": feat["sharp_divergence"],
        "vw": liq["volume_weight"],
        "coverage": feat["drift_coverage"],
    }


# 单场网络超时（秒）：代理偶发 stall（DNS/CONNECT hang，urlopen 的 timeout
# 覆盖不到），超时则该场跳过，整轮不卡死。
_TITAN_MATCH_TIMEOUT = 180


def titan_backtest(max_matches: int = 80, delay: float = 0.4):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    src = Titan007Source(request_delay=delay)
    ids = src._list_match_ids()
    print(f"BaSID 共 {len(ids)} 个 ID，逐个检查完赛场次…", flush=True)
    ev = Eval()
    drifts, divs, vws, cover = [], [], [], {}
    n_ok = n_skip = n_nodata = n_timeout = 0
    for mid in ids:
        if n_ok >= max_matches:
            break
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_eval_one_titan, mid, src)
            try:
                status, data = fut.result(timeout=_TITAN_MATCH_TIMEOUT)
            except TimeoutError:
                n_timeout += 1
                continue
        except Exception:
            n_skip += 1
            continue
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        if status == "skip":
            continue
        if status == "nodata":
            n_nodata += 1
            continue
        if status == "error":
            n_skip += 1
            continue
        ev.add(data["t1"], data["t2"], data["t3"], data["outcome"])
        n_ok += 1
        if data["drift_home"] is not None:
            drifts.append(data["drift_home"])
        if data["div"] is not None:
            divs.append(data["div"])
        vws.append(data["vw"])
        cover[data["coverage"]] = cover.get(data["coverage"], 0) + 1
        if n_ok % 10 == 0:
            print(f"  已评估 {n_ok} 场…", flush=True)
    diag = {
        "polymarket_回测视为缺失_降级公司数量": True,
        "跳过_引擎异常": n_skip,
        "跳过_网络超时": n_timeout,
        "跳过_数据不足": n_nodata,
        "drift_覆盖": cover,
        "平均|drift_home|": round(sum(drifts) / len(drifts), 4) if drifts else None,
        "平均sharp_divergence": round(sum(divs) / len(divs), 4) if divs else None,
        "平均volume_weight": round(sum(vws) / len(vws), 4) if vws else None,
    }
    return ev.report(), diag


def main() -> int:
    ap = argparse.ArgumentParser(description="资金流向 walk-forward 评估")
    ap.add_argument("--db", action="store_true",
                    help="用 predictions⋈settlements（需 DATABASE_URL）")
    ap.add_argument("--titan-backtest", action="store_true",
                    help="抓 Titan007 已完赛场次做无泄漏回测")
    ap.add_argument("--max-matches", type=int, default=80)
    ap.add_argument("--out", default="/tmp/flow_walkforward_report.json")
    args = ap.parse_args()
    if args.db:
        if psycopg is None:
            print("未安装 psycopg"); return 1
        db = os.environ.get("DATABASE_URL")
        if not db:
            print("DATABASE_URL 未设置"); return 1
        rep, diag = eval_db(db)
    elif args.titan_backtest:
        rep, diag = titan_backtest(max_matches=args.max_matches)
    else:
        ap.print_help()
        return 1
    _print_report(rep, diag)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"report": rep, "diagnostics": diag,
                   "generated_at": datetime.now(timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=2)
    print(f"报告已存 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
