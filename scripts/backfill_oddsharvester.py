#!/usr/bin/env python3
"""
OddsHarvester 低级别联赛历史赔率批量回填（不进每日链）

用 PyPI `oddsharvester` 包（MIT，jordantete/OddsHarvester）按 historic 模式
批量抓取 OddsPortal 低级别联赛历史赔率（1x2 / 亚盘 / 大小球），输出
JSON/CSV 存 data/backfill/oddsharvester/。

用法示例:
    # 小样本试跑: J2 最近 1 个赛季 1x2，预览模式（只看可见盘口，更快）
    python3 scripts/backfill_oddsharvester.py --leagues japan-j2-j3-league \\
        --seasons 2025 --markets 1x2 --preview-only --max-pages 2

    # 全量回填（分批跑，不要一次全跑）:
    python3 scripts/backfill_oddsharvester.py --seasons 2023,2024,2025 \\
        --markets 1x2,asian_handicap,over_under

    # 只补某个联赛/赛季:
    python3 scripts/backfill_oddsharvester.py --leagues brazil-serie-b \\
        --seasons 2024 --markets over_under --no-resume

环境说明:
- 依赖 Playwright + Chromium（本机已装好 chromium-1243）。
- 本机（Hatch VM）出口走 MITM 代理: 实测 Chromium 直接走代理会
  ERR_EMPTY_RESPONSE（curl 正常），属环境限制；本脚本自动给浏览器
  加 --ignore-certificate-errors 并从环境变量读代理（https_proxy）。
  若在 Hatch VM 上仍跑不通，属代理指纹拦截，改去阿里云跑
  （阿里云直连 egress，无此问题）。
- 抓取纪律: 默认 --request-delay 2s、并发 1；不要调高并发打源站。
"""

import argparse
import json
import os
import sys
import time

BASE_DIR = os.path.expanduser("~/workspace/football-prediction-v2")
OUT_DIR = os.path.join(BASE_DIR, "data", "backfill", "oddsharvester")

# 目标低级别联赛（oddsharvester 内置联赛 key，已核实 2026-10-06）
LEAGUES = [
    "japan-j2-j3-league",
    "south-korea-k-league-2",
    "norway-obos-ligaen",
    "sweden-superettan",
    "brazil-serie-b",
]
DEFAULT_MARKETS = ["1x2", "asian_handicap", "over_under"]


def _patch_for_mitm_proxy():
    """
    MITM 出口代理环境（如 Hatch VM）: 浏览器忽略证书错误。
    直连环境下加这个 flag 无副作用。
    """
    from oddsharvester.utils import constants
    args = list(constants.PLAYWRIGHT_BROWSER_ARGS)
    if "--ignore-certificate-errors" not in args:
        args.append("--ignore-certificate-errors")
    constants.PLAYWRIGHT_BROWSER_ARGS = args


def _proxy_url():
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = os.environ.get(var)
        if v:
            return v
    return None


def run_combo(league, season, markets, out_path, args):
    """跑一个（联赛, 赛季）组合；返回 (ok, info)。"""
    from oddsharvester.cli.cli import main as oh_main

    argv = ["oddsharvester", "historic",
            "-s", "football", "-l", league,
            "--season", season,
            "-m", ",".join(markets),
            "-f", args.format, "-o", out_path,
            "--headless",
            "-c", str(args.concurrency),
            "--request-delay", str(args.request_delay),
            "--max-pages", str(args.max_pages)]
    proxy = _proxy_url()
    if proxy:
        argv += ["--proxy-url", proxy]
    if args.preview_only:
        argv.append("--preview-only")
    else:
        argv.append("--full-scrape")
    if args.odds_history:
        argv.append("--odds-history")

    t0 = time.time()
    old_argv = sys.argv
    sys.argv = argv
    try:
        oh_main()
    except SystemExit as e:
        ok = (e.code == 0)
        info = f"exit={e.code}"
    except Exception as e:
        ok = False
        info = f"exception: {str(e)[:200]}"
    else:
        ok = True
        info = "done"
    finally:
        sys.argv = old_argv
    dt = time.time() - t0
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size == 0:
        ok = False
        info += " (输出为空/缺失)"
    return ok, f"{info} 用时{dt:.0f}s 输出{size}字节"


def main():
    ap = argparse.ArgumentParser(description="OddsHarvester 低级别联赛历史赔率批量回填")
    ap.add_argument("--leagues", default=",".join(LEAGUES),
                    help="逗号分隔的联赛 key（默认 5 个低级别联赛）")
    ap.add_argument("--seasons", default="2025",
                    help="逗号分隔赛季，如 2023,2024,2025（historic 必填）")
    ap.add_argument("--markets", default=",".join(DEFAULT_MARKETS),
                    help="逗号分隔玩法（默认 1x2, asian_handicap, over_under）")
    ap.add_argument("--format", default="json", choices=["json", "csv"])
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--max-pages", type=int, default=0,
                    help="每组合最大页数（0=不限；试跑用小数字）")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--request-delay", type=float, default=2.0)
    ap.add_argument("--preview-only", action="store_true",
                    help="只抓可见盘口（更快；试跑用）")
    ap.add_argument("--odds-history", action="store_true",
                    help="同时抓赔率走势（更慢，数据量更大）")
    ap.add_argument("--no-resume", action="store_true",
                    help="默认跳过已存在的输出文件；加此 flag 强制重跑")
    ns = ap.parse_args()

    _patch_for_mitm_proxy()
    os.makedirs(ns.outdir, exist_ok=True)

    leagues = [l.strip() for l in ns.leagues.split(",") if l.strip()]
    seasons = [s.strip() for s in ns.seasons.split(",") if s.strip()]
    markets = [m.strip() for m in ns.markets.split(",") if m.strip()]
    combos = [(l, s) for l in leagues for s in seasons]
    print(f"共 {len(combos)} 个组合: {len(leagues)}联赛 x {len(seasons)}赛季, "
          f"玩法={markets}")

    summary = {"leagues": leagues, "seasons": seasons, "markets": markets,
               "combos": []}
    for league, season in combos:
        league_dir = os.path.join(ns.outdir, league, season)
        os.makedirs(league_dir, exist_ok=True)
        out_path = os.path.join(
            league_dir, f"{'_'.join(markets)}.{ns.format}")
        if os.path.exists(out_path) and not ns.no_resume:
            print(f"[SKIP] {league}/{season} 已存在（--no-resume 强制重跑）")
            summary["combos"].append({"league": league, "season": season,
                                      "status": "skipped"})
            continue
        print(f"[RUN ] {league}/{season} -> {out_path}")
        try:
            ok, info = run_combo(league, season, markets, out_path, ns)
        except Exception as e:
            ok, info = False, f"wrapper exception: {str(e)[:200]}"
        print(f"[{'OK ' if ok else 'FAIL'}] {league}/{season}: {info}")
        summary["combos"].append({"league": league, "season": season,
                                  "status": "ok" if ok else "failed",
                                  "info": info})
        time.sleep(3)  # 组合之间喘息，别打源站

    summ_path = os.path.join(ns.outdir,
                             f"_summary_{int(time.time())}.json")
    with open(summ_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    n_ok = sum(1 for c in summary["combos"] if c["status"] == "ok")
    print(f"\n完成 {n_ok}/{len(combos)}，汇总: {summ_path}")


if __name__ == "__main__":
    main()
