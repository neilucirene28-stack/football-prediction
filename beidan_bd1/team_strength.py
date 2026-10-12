"""Independent, unvalidated BD-1 L1 team attack/defence shadow candidate.

Only canonical, verified team/competition IDs and past available results may train
this route. Hyperparameters are fixed candidates until strict walk-forward tuning.
"""
from __future__ import annotations

from collections import Counter
import math

from .baseline import _history, _poisson_matrix, _vectors_from_matrix
from .snapshot import _datetime, _MATCH_ID


MODEL_VERSION = "bd1-l1-ridge-poisson-shadow-0.1"


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _MATCH_ID.fullmatch(value):
        raise ValueError(f"{field} 须为已核验规范ID")
    return value


def predict_l1(history: list[dict], *, asof_at: str, kickoff_at: str,
               home_id: str, away_id: str, competition_id: str,
               handicap: int | None, ridge: float = 5.0,
               min_history: int = 30, min_team: int = 5) -> dict:
    """Fit penalized Poisson log attack/defence effects in the same competition.

    log λ_home = log μ_home + attack[home] + defence[away]; the away
    expression reverses the teams. Coordinate Newton updates optimize the
    Poisson log likelihood with 0.5 * ridge * sum(effect²) penalty.
    """
    asof = _datetime(asof_at, "asof_at")
    if asof >= _datetime(kickoff_at, "kickoff_at"):
        raise ValueError("预测时点必须早于开球")
    home_id = _identifier(home_id, "home_id")
    away_id = _identifier(away_id, "away_id")
    competition_id = _identifier(competition_id, "competition_id")
    if home_id == away_id:
        raise ValueError("对阵双方规范ID相同")
    if handicap is not None and (not isinstance(handicap, int) or isinstance(handicap, bool)):
        raise ValueError("让球线必须为整数或 null")
    if not isinstance(ridge, (int, float)) or isinstance(ridge, bool) or not math.isfinite(ridge) or ridge <= 0:
        raise ValueError("ridge 必须为正有限数")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in (min_history, min_team)):
        raise ValueError("最低样本数必须为正整数")
    # _history enforces both result availability and actual verification clocks.
    rows = [r for r in _history(history, asof)
            if r.get("competition_id") == competition_id
            and r.get("identity_verified") is True
            and isinstance(r.get("identity_source"), str)
            and bool(r.get("identity_source"))
            and r.get("identity_verified_at") is not None
            and _datetime(r["identity_verified_at"], "identity_verified_at") <= asof
            and all(isinstance(r.get(k), str) and _MATCH_ID.fullmatch(r[k])
                    for k in ("home_id", "away_id"))]
    if len(rows) < min_history:
        raise ValueError(f"L1同赛事可用规范ID历史仅{len(rows)}场，低于{min_history}场")
    counts = Counter(t for r in rows for t in (r["home_id"], r["away_id"]))
    if min(counts[home_id], counts[away_id]) < min_team:
        raise ValueError("L1对阵双方规范ID历史样本不足")
    mu_h = (sum(r["ft_home"] for r in rows) + .5) / len(rows)
    mu_a = (sum(r["ft_away"] for r in rows) + .5) / len(rows)
    if mu_h > 8 or mu_a > 8:
        raise ValueError("L1赛事进球均值超出数值预算")
    attack = {t: 0.0 for t in counts}
    defence = {t: 0.0 for t in counts}
    # Synchronous damped diagonal Newton; ridge ensures identifiability.
    for _ in range(80):
        ga = {t: -ridge * attack[t] for t in counts}
        gd = {t: -ridge * defence[t] for t in counts}
        ha = {t: ridge for t in counts}
        hd = {t: ridge for t in counts}
        for r in rows:
            h, a = r["home_id"], r["away_id"]
            eh = mu_h * math.exp(attack[h] + defence[a])
            ea = mu_a * math.exp(attack[a] + defence[h])
            ga[h] += r["ft_home"] - eh
            gd[a] += r["ft_home"] - eh
            ga[a] += r["ft_away"] - ea
            gd[h] += r["ft_away"] - ea
            ha[h] += eh
            hd[a] += eh
            ha[a] += ea
            hd[h] += ea
        delta = 0.0
        for t in counts:
            da = max(-.2, min(.2, .5 * ga[t] / ha[t]))
            dd = max(-.2, min(.2, .5 * gd[t] / hd[t]))
            attack[t] = max(-1.5, min(1.5, attack[t] + da))
            defence[t] = max(-1.5, min(1.5, defence[t] + dd))
            delta = max(delta, abs(da), abs(dd))
        if delta < 1e-9:
            break
    lam_h = mu_h * math.exp(attack[home_id] + defence[away_id])
    lam_a = mu_a * math.exp(attack[away_id] + defence[home_id])
    matrix = _poisson_matrix(lam_h, lam_a)
    total_h = sum(r["ft_home"] for r in rows)
    total_a = sum(r["ft_away"] for r in rows)
    qh = (sum(r["ht_home"] for r in rows) + .5) / (total_h + 1)
    qa = (sum(r["ht_away"] for r in rows) + .5) / (total_a + 1)
    vectors, score31 = _vectors_from_matrix(matrix, qh, qa, handicap)
    return {
        "status": "shadow", "model_version": MODEL_VERSION,
        "route": "L1_team_strength", "parameters_unvalidated": True,
        "family_status": "competition_id_verified", "training_n": len(rows),
        "training_match_ids": [r["match_id"] for r in rows],
        "competition_id": competition_id, "home_id": home_id,
        "away_id": away_id, "ridge": ridge,
        "lambda_home": lam_h, "lambda_away": lam_a,
        "ht_fractions": {"home": qh, "away": qa},
        "handicap": handicap, "vectors": vectors, "score_31": score31,
    }
