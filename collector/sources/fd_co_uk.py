"""
football-data.co.uk CSV 接入
https://www.football-data.co.uk/mmz4281/{赛季}/{联赛代码}.csv

完全免费，无需key
注意: 必须跟随重定向 (-L)，www.football-data.co.uk → football-data.co.uk
核心价值: 17个欧洲联赛历史数据，比分+射门/角球/牌+多家博彩公司初盘/收盘赔率
注意: 2026-10-01实测表头120列，无HxG/AxG列，xG不可用
"""

import csv
import os
import subprocess
import tempfile

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# 联赛代码
LEAGUE_CODES = {
    "英超": "E0", "英冠": "E1", "英甲": "E2", "英乙": "E3",
    "西甲": "SP1", "西乙": "SP2",
    "德甲": "D1", "德乙": "D2",
    "意甲": "I1", "意乙": "I2",
    "法甲": "F1", "法乙": "F2",
    "荷甲": "N1", "葡超": "P1", "希腊超": "G1", "土超": "T1", "比甲": "B1",
}

# 赛季格式: "2627" 表示 2026/27 赛季


def download_csv(league_code, season, save_path=None):
    """
    下载某联赛某赛季的CSV
    league_code: 如 "E0" 或中文名 "英超"
    season: 如 "2627"
    返回: CSV文件路径
    """
    code = LEAGUE_CODES.get(league_code, league_code)
    url = f"{BASE_URL}/{season}/{code}.csv"

    if save_path is None:
        fd, save_path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)

    cmd = [
        "curl", "-s", "-L", "--max-time", "30",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        "-o", save_path, url,
    ]
    subprocess.run(cmd, timeout=40, check=True)

    # 验证文件有效
    size = os.path.getsize(save_path)
    if size < 1000:
        raise RuntimeError(f"CSV下载异常，文件仅 {size} 字节: {url}")
    return save_path


def parse_csv(csv_path):
    """
    解析CSV，返回比赛列表
    包含: 日期、主客队、比分、xG、赔率
    """
    matches = []
    with open(csv_path, encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            m = {
                "date": row.get("Date", ""),
                "home": row.get("HomeTeam", ""),
                "away": row.get("AwayTeam", ""),
                "score_home": _int(row.get("FTHG")),
                "score_away": _int(row.get("FTAG")),
                "score_ht_home": _int(row.get("HTHG")),
                "score_ht_away": _int(row.get("HTAG")),
                "result": row.get("FTR", ""),
                # 射门/角球/牌
                "shots_home": _int(row.get("HS")),
                "shots_away": _int(row.get("AS")),
                "corners_home": _int(row.get("HC")),
                "corners_away": _int(row.get("AC")),
                # 赔率 (Bet365初盘)
                "odds_home": _float(row.get("B365H")),
                "odds_draw": _float(row.get("B365D")),
                "odds_away": _float(row.get("B365A")),
                # Pinnacle收盘
                "odds_close_home": _float(row.get("PSCH")),
                "odds_close_draw": _float(row.get("PSCD")),
                "odds_close_away": _float(row.get("PSCA")),
            }
            matches.append(m)
    return matches


def _int(v):
    try:
        return int(v) if v else None
    except (ValueError, TypeError):
        return None


def _float(v):
    try:
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def get_league_data(league_code, season, save_dir=None):
    """
    一站式: 下载+解析+可选保存
    返回: 比赛列表
    """
    csv_path = download_csv(league_code, season)
    try:
        matches = parse_csv(csv_path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            code = LEAGUE_CODES.get(league_code, league_code)
            dest = os.path.join(save_dir, f"{code}_{season}.csv")
            os.replace(csv_path, dest)
            csv_path = dest
        return matches
    finally:
        # 如果没保存，清理临时文件
        if save_dir is None and os.path.exists(csv_path):
            os.unlink(csv_path)


if __name__ == "__main__":
    print("=== football-data.co.uk 测试 ===")
    matches = get_league_data("英超", "2526")
    print(f"英超 25/26赛季: {len(matches)}场")
    if matches:
        m = matches[0]
        print(f"例: {m['date']} {m['home']} {m['score_home']}-{m['score_away']} {m['away']}")
        print(f"  赔率: {m['odds_home']}/{m['odds_draw']}/{m['odds_away']}")
