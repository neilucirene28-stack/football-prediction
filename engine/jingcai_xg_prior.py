"""Understat xG-based attack/defense priors for lambda estimation.

When recent form data is sparse (degraded), the default shrinkage prior of 1.0
(league average) flattens all teams to the same lambda. This module provides
team-specific priors derived from the completed 2025/26 season xG data,
allowing strong teams (e.g. Barcelona, Bayern) to retain higher lambdas
even with limited current-season form data.

Data source: data/xg_prior_202526.json (Understat, 5 leagues, 96 teams).
Temporal note: 2025/26 season ended May 2026; data was fully known before
Oct 2026 predictions. No lookahead bias for 2026/27 season predictions.
"""
import json
import os

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "xg_prior_202526.json")

# Chinese -> Understat English team name mapping (5 leagues only)
CN_EN = {
    '阿森纳': 'arsenal', '利兹联': 'leeds', '利兹': 'leeds',
    '切尔西': 'chelsea', '伯恩茅斯': 'bournemouth',
    '维拉': 'aston villa', '阿斯顿维拉': 'aston villa',
    '布伦特': 'brentford', '布伦特福德': 'brentford',
    '伊普斯': 'ipswich', '伊普斯维奇': 'ipswich', '富勒姆': 'fulham',
    '曼联': 'manchester united', '热刺': 'tottenham', '托特纳姆热刺': 'tottenham',
    '埃弗顿': 'everton', '水晶宫': 'crystal palace',
    '诺丁汉': 'nottingham forest', '诺丁汉森林': 'nottingham forest',
    '桑德兰': 'sunderland', '狼队': 'wolverhampton wanderers',
    '西汉姆': 'west ham', '布莱顿': 'brighton',
    '巴列卡诺': 'rayo vallecano', '毕尔巴鄂': 'athletic club', '毕尔巴鄂竞技': 'athletic club',
    '阿拉维斯': 'alaves', '马竞': 'atletico madrid', '马德里竞技': 'atletico madrid',
    '巴萨': 'barcelona', '巴塞罗那': 'barcelona', '赫塔费': 'getafe',
    '皇马': 'real madrid', '皇家马德里': 'real madrid', '比利亚雷亚尔': 'villarreal',
    '埃尔切': 'elche', '塞尔塔': 'celta vigo', '塞尔塔维戈': 'celta vigo',
    '皇家社会': 'real sociedad',
    '贝蒂斯': 'real betis', '皇家贝蒂斯': 'real betis', '奥萨苏纳': 'osasuna',
    '莱万特': 'levante', '塞维利亚': 'sevilla', '巴伦西亚': 'valencia',
    '马洛卡': 'mallorca', '赫罗纳': 'girona', '西班牙人': 'espanyol',
    '热那亚': 'genoa', '佛罗伦萨': 'fiorentina',
    '国际米兰': 'inter', '国米': 'inter', '帕尔马': 'parma calcio 1913',
    '那不勒斯': 'napoli',
    '拉齐奥': 'lazio', '蒙扎': 'monza',
    '莱切': 'lecce', '博洛尼亚': 'bologna',
    '萨索洛': 'sassuolo', 'AC米兰': 'ac milan',
    '卡利亚里': 'cagliari', '尤文图斯': 'juventus', '尤文': 'juventus',
    '科莫': 'como', '罗马': 'roma',
    '都灵': 'torino', '乌迪内斯': 'udinese',
    '亚特兰大': 'atalanta', '维罗纳': 'verona', '恩波利': 'empoli',
    '柏林联合': 'union berlin',
    '奥格斯堡': 'augsburg', '拜仁': 'bayern munich', '拜仁慕尼黑': 'bayern munich',
    '美因茨': 'mainz 05', '勒沃库森': 'bayer leverkusen',
    '斯图加特': 'vfb stuttgart',
    '霍芬海姆': 'hoffenheim', '汉堡': 'hamburger sv',
    '莱红牛': 'rasenballsport leipzig', '法兰克福': 'eintracht frankfurt',
    '科隆': 'fc cologne', '门兴': 'borussia m.gladbach',
    '弗赖堡': 'freiburg', '不莱梅': 'werder bremen', '沃尔夫斯堡': 'wolfsburg',
    '多特': 'borussia dortmund', '多特蒙德': 'borussia dortmund',
    '里尔': 'lille', '勒阿弗尔': 'le havre',
    '摩纳哥': 'monaco', '图卢兹': 'toulouse',
    '尼斯': 'nice', '斯特拉斯': 'strasbourg', '斯特拉斯堡': 'strasbourg',
    '特鲁瓦': 'troyes', '马赛': 'marseille', '梅斯': 'metz',
    '利物浦': 'liverpool', '曼城': 'manchester city',
    '纽卡斯尔': 'newcastle united', '纽卡': 'newcastle united',
    '巴黎': 'paris saint germain', '巴黎圣日耳曼': 'paris saint germain',
    '朗斯': 'lens', '里昂': 'lyon',
}

_XG_CACHE = None
_LEAGUE_AVG_CACHE = None


def _load():
    global _XG_CACHE, _LEAGUE_AVG_CACHE
    if _XG_CACHE is not None:
        return _XG_CACHE, _LEAGUE_AVG_CACHE
    try:
        with open(_DATA_FILE, encoding='utf-8') as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _XG_CACHE, _LEAGUE_AVG_CACHE = {}, {}
        return _XG_CACHE, _LEAGUE_AVG_CACHE
    xg = {}
    lavg = {}
    for lg, teams in raw.get('leagues', {}).items():
        if not teams:
            continue
        sxg = sum(t['xg_per_match'] for t in teams) / len(teams)
        sxga = sum(t['xga_per_match'] for t in teams) / len(teams)
        lavg[lg] = (sxg, sxga)
        for t in teams:
            xg[t['team'].lower()] = (t['xg_per_match'], t['xga_per_match'], lg)
    _XG_CACHE, _LEAGUE_AVG_CACHE = xg, lavg
    return xg, lavg


def get_xg_prior(cn_name):
    """Return (attack_prior, defense_prior) for a Chinese team name, or None.

    attack_prior > 1.0 means stronger-than-average attack.
    defense_prior > 1.0 means leakier-than-average defense.
    """
    xg, lavg = _load()
    en = CN_EN.get(cn_name, '').lower()
    if en not in xg:
        return None
    xg_pm, xga_pm, lg = xg[en]
    lg_xg, lg_xga = lavg[lg]
    # Clamp to avoid extreme priors from small-sample noise
    attack = min(max(xg_pm / lg_xg, 0.5), 2.0)
    defense = min(max(xga_pm / lg_xga, 0.5), 2.0)
    return (attack, defense)
