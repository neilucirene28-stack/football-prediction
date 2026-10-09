"""
openfootball 接入 — football.json (openfootball org, 公共领域 Public Domain)
https://github.com/openfootball/football.json

数据结构: {season}/{league}.json
{
  "name": "Brasileiro Série A 2026",
  "matches": [
    {"round": "Matchday 1", "date": "2026-01-28", "time": "19:00",
     "team1": "CA Mineiro", "team2": "SE Palmeiras",
     "score": {"ht": [1, 1], "ft": [2, 2]}}
  ]
}
未开赛场次没有 "score" 键。

联赛代码: en.1(英超) es.1(西甲) it.1(意甲) de.1(德甲) fr.1(法甲) nl.1(荷甲) pt.1(葡超)
          br.1(巴甲) br.2(巴乙) ar.1(阿甲) cn.1(中超) jp.1(J1) mls(美职)
          uefa.cl(欧冠) en.2(英冠) es.2(西乙) de.2(德乙) it.2(意乙) fr.2(法乙)
          sco.1(苏超) be.1(比甲) at.1(奥甲) tr.1(土超) gr.1(希超) mx.1(墨超)
          copa.l(解放者杯)

重要发现 (2026-10-09 实测):
- 任务原定的 openfootball/football.db 只有俱乐部/球场数据(.clubs.txt),
  无比分；已 clone 在 data/football.db/。真正的比分库是 data/football.json/。
- 巴甲 br.1 在 football.json 只有 2019/2020/2025/2026
  (2021-2024 上游缺失；完整历史在 openfootball/south-america 的
  Football.TXT 文本，暂未接入，如需可后续加 TXT 解析)。
- 赛季目录: 欧洲冬歇联赛用 "2026-27", 历年制联赛(巴甲等)用 "2026"。
- 只读本地文件，零网络；更新走 git pull (sync())。
"""

import json
import re
import subprocess
from pathlib import Path

# 数据仓库根目录: collector/sources/../.. / data / football.json
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "football.json"
# 俱乐部库 (无比分，仅留作队名/别名参考，暂未使用)
CLUBS_DIR = Path(__file__).resolve().parents[2] / "data" / "football.db"

# 中文名 -> 代码 (实测 2026-10-09)
LEAGUE_CODES = {
    # 五大联赛
    "英超": "en.1", "西甲": "es.1", "意甲": "it.1", "德甲": "de.1", "法甲": "fr.1",
    # 次级联赛
    "英冠": "en.2", "西乙": "es.2", "德乙": "de.2", "意乙": "it.2", "法乙": "fr.2",
    "英甲": "en.3", "英乙": "en.4",
    # 荷葡苏比奥
    "荷甲": "nl.1", "葡超": "pt.1", "苏超": "sco.1", "比甲": "be.1", "奥甲": "at.1",
    "土超": "tr.1", "希超": "gr.1",
    # 美洲
    "巴西甲": "br.1", "巴甲": "br.1", "巴西乙": "br.2", "巴乙": "br.2",
    "阿甲": "ar.1", "哥甲": "co.1", "墨超": "mx.1", "美职": "mls",
    "解放者杯": "copa.l",
    # 亚洲
    "J1": "jp.1", "中超": "cn.1",
    # 欧战
    "欧冠": "uefa.cl",
}

_SEASON_RE = re.compile(r"^\d{4}(-\d{2})?$")


def _norm_code(league_code):
    return LEAGUE_CODES.get(league_code, league_code)


def _candidate_seasons(season):
    """把用户给的赛季展开成候选目录名。'2026' -> ['2026', '2026-27']"""
    if _SEASON_RE.match(season):
        if len(season) == 4:
            nxt = f"{season}-{str(int(season) + 1)[-2:]}"
            return [season, nxt]
        return [season]
    return [season]


def _resolve_path(code, season):
    """找到本地 JSON 文件；找不到返回 None。"""
    for s in _candidate_seasons(season):
        p = DATA_DIR / s / f"{code}.json"
        if p.is_file():
            return p, s
    return None, None


def _parse_matches(doc, code, season_dir):
    """把 football.json 的 matches 解析成统一结构。"""
    out = []
    for m in doc.get("matches", []):
        home = m.get("team1", "")
        away = m.get("team2", "")
        if not home or not away:
            continue
        score = m.get("score") or {}
        if isinstance(score, dict):
            # {"ht": [h,a], "ft": [h,a]}
            ft = score.get("ft") or []
            ht = score.get("ht") or []
        elif isinstance(score, (list, tuple)):
            # [h, a] 全场比分简写 (无半场)
            ft, ht = list(score), []
        else:
            ft, ht = [], []
        out.append({
            "date": m.get("date", ""),
            "time": m.get("time", ""),
            "round": m.get("round", ""),
            "home": home,
            "away": away,
            "score_home": ft[0] if len(ft) >= 2 else None,
            "score_away": ft[1] if len(ft) >= 2 else None,
            "ht_home": ht[0] if len(ht) >= 2 else None,
            "ht_away": ht[1] if len(ht) >= 2 else None,
            "played": len(ft) >= 2,
            "league": code,
            "season": season_dir,
        })
    return out


def get_results(league_code, season, include_unplayed=False):
    """
    获取某联赛某赛季比分。
    league_code: 中文名("巴甲")或代码("br.1")
    season: "2026"(历年制) / "2026-27"(冬歇制)；传 "2026" 会自动试 "2026-27"
    返回: [{date, time, round, home, away, score_home, score_away,
            ht_home, ht_away, played, league, season}]
    默认只返回已赛场次；include_unplayed=True 时也返回未赛(比分 None)。
    本地无数据时返回 []。
    """
    code = _norm_code(league_code)
    path, season_dir = _resolve_path(code, season)
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    matches = _parse_matches(doc, code, season_dir)
    if not include_unplayed:
        matches = [m for m in matches if m["played"]]
    return matches


def list_leagues():
    """
    扫描本地数据仓库，返回可用联赛。
    返回: [{code, name(中文名,未知则为code), seasons:[...]}] 按 code 排序。
    """
    if not DATA_DIR.is_dir():
        return []
    code2seasons = {}
    for season_dir in sorted(DATA_DIR.iterdir()):
        if not season_dir.is_dir() or not _SEASON_RE.match(season_dir.name):
            continue
        for f in sorted(season_dir.glob("*.json")):
            code = f.stem
            if code.endswith("-full"):  # at.1-full 这类全量变体跳过
                continue
            code2seasons.setdefault(code, []).append(season_dir.name)
    cn = {v: k for k, v in LEAGUE_CODES.items()}
    return [
        {"code": code, "name": cn.get(code, code), "seasons": seasons}
        for code, seasons in sorted(code2seasons.items())
    ]


def _git_pull(repo_dir):
    """对单个数据仓库执行 git pull --ff-only，返回状态 dict。"""
    if not (repo_dir / ".git").is_dir():
        return {"ok": False, "repo": repo_dir.name, "error": "not a git repo"}
    try:
        before = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir,
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        r = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=repo_dir,
            capture_output=True, text=True, timeout=300,
        )
        after = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir,
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return {
            "ok": r.returncode == 0,
            "repo": repo_dir.name,
            "updated": before != after,
            "before": before,
            "after": after,
            "output": (r.stdout or r.stderr).strip()[:500],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "repo": repo_dir.name, "error": str(e)}


def sync():
    """
    更新本地数据仓库 (data/football.json/ + data/football.db/ git pull)。
    返回: {"football.json": {...}, "football.db": {...}} 每个含 ok/updated/before/after。
    供 cron 定期调用 (见 scripts/sync_openfootball.sh)。
    """
    return {
        "football.json": _git_pull(DATA_DIR),
        "football.db": _git_pull(CLUBS_DIR),
    }


if __name__ == "__main__":
    print("=== openfootball/football.json 本地数据 ===")
    leagues = list_leagues()
    print(f"联赛数: {len(leagues)}")
    for lg in leagues:
        print(f"  {lg['code']:10s} {lg['name']:6s} 赛季: {lg['seasons'][0]}..{lg['seasons'][-1]} ({len(lg['seasons'])}个)")
    print()
    for cn_name, season in [("巴甲", "2026"), ("英超", "2026")]:
        rs = get_results(cn_name, season)
        print(f"{cn_name} {season}: {len(rs)} 场已赛")
        if rs:
            m = rs[0]
            print(f"  例: {m['date']} {m['home']} {m['score_home']}-{m['score_away']} {m['away']}")
