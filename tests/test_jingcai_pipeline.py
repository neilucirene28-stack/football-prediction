"""Regression coverage for confirmed Jingcai pipeline defects."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from engine.jingcai_handicap import handicap_view, integer_handicap
from engine.predictor import PredictError, predict
from engine.poisson import (score_matrix, match_probs, handicap_1x2,
                            top_scores, total_goals_exact, expected_total_goals,
                            half_full_1x2, btts_prob)
from scripts.scoreboard import score_one
from scripts.jingcai_format import format_handicap_1x2
from api.api.persist import build_prediction_row


def payload(**kwargs):
    now = datetime.now(timezone.utc)
    p = {"home": "主队", "away": "客队", "competition": "测试联赛",
         "kickoff_at": (now + timedelta(days=2)).isoformat(),
         "snapshot_at": (now - timedelta(seconds=1)).isoformat(),
         "home_recent": [{"gf": 2, "ga": 1, "venue": "H"}] * 8,
         "away_recent": [{"gf": 1, "ga": 1, "venue": "A"}] * 8,
         "odds": {"home": 1.8, "draw": 3.5, "away": 4.5},
         "handicap_line": -1, "ou_line": 2.5, "asian": {"handicap": -.5}}
    p.update(kwargs)
    return p


@pytest.mark.parametrize("rq", [-2, -1, 0, 1, 2])
def test_all_jingcai_full_time_derivatives_share_final_matrix(rq):
    r = predict(payload(handicap_line=rq), {"mc_min_score": 101})
    m, d = r["score_matrix_full"], r["derivatives"]
    hp = handicap_1x2(m, rq)
    assert hp == pytest.approx(tuple(d["handicap_1x2"][f"p_{k}_full"] for k in ("home", "draw", "away")), abs=1e-12)
    assert match_probs(m) == pytest.approx(r["p_final_full"], abs=1e-12)
    assert d["top_scores"] == [{"score": s, "prob": round(p, 4)} for s, p in top_scores(m, n=5)]
    assert d["total_goals_exact"] == {str(k): round(v, 4) for k, v in total_goals_exact(m).items()}
    assert d["expected_goals"] == round(expected_total_goals(m), 2)
    assert d["btts"] == round(btts_prob(m), 4)
    hf = half_full_1x2(r["lambda_home_full"], r["lambda_away_full"], rho=-.13,
                       ft_matrix=m, strict_ft_grid=True)
    assert hf == pytest.approx(d["half_full_1x2_full"], abs=1e-12)
    h = d["handicap_1x2"]
    assert h["pick_rule"] == "argmax"
    assert h["risk_view"]["top1"] == h["pick"]


def test_threshold_does_not_override_more_likely_outcome():
    r = predict(payload(competition="欧冠"), {"letdraw_strength": 1.0, "mc_min_score": 101})
    h = r["derivatives"]["handicap_1x2"]
    assert h["p_draw_full"] >= .28
    assert h["pick"] == max(("home", "draw", "away"), key=lambda k: h[f"p_{k}_full"])
    assert h["pick"] != "draw"


def test_argmax_can_naturally_select_handicap_draw_below_fifty_percent():
    with patch("engine.predictor.estimate_lambdas", return_value=(1.2, .02, {})):
        r = predict(payload(odds=None), {"rho": 0, "letdraw_strength": 0, "mc_min_score": 101})
    assert r["derivatives"]["handicap_1x2"]["pick"] == "draw"


def test_future_snapshot_rejected_in_live_and_virtual_time():
    p = payload()
    now = datetime.now(timezone.utc)
    p["snapshot_at"] = (now + timedelta(hours=1)).isoformat()
    with pytest.raises(PredictError, match="截止"):
        predict(p)
    with pytest.raises(PredictError, match="截止"):
        predict(p, asof=now)


@pytest.mark.parametrize("bad", [-.5, .25, True, "-1.5", float("nan")])
def test_fractional_and_invalid_jingcai_handicap_is_rejected(bad):
    with pytest.raises(PredictError, match="整数"):
        predict(payload(handicap_line=bad))


def test_prior_from_future_cannot_enter_historical_replay():
    p = payload(kickoff_at="2026-09-10T20:00:00+08:00", snapshot_at="2026-09-10T10:00:00+08:00")
    asof = datetime.fromisoformat("2026-09-10T11:00:00+08:00")
    with pytest.raises(PredictError, match="先验"):
        predict(p, asof=asof)
    r = predict(p, {"letdraw_strength": 0, "mc_min_score": 101}, asof=asof)
    assert r["evaluation_mode"] == "historical_replay"
    with pytest.raises(ValueError, match="重放"):
        build_prediction_row(1, p, r)


def test_actual_generation_time_is_persisted_instead_of_snapshot_time():
    p = payload(snapshot_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    r = predict(p, {"mc_min_score": 101})
    assert datetime.fromisoformat(r["prediction_generated_at"]) > datetime.fromisoformat(p["snapshot_at"])
    assert build_prediction_row(1, p, r)["predicted_at"] == r["prediction_generated_at"]


@pytest.mark.parametrize("rq,score,actual", [(-1, (1, 0), 1), (-2, (2, 0), 1),
                                            (1, (0, 1), 1), (-1, (1, 1), 2),
                                            (-1, (3, 0), 0)])
def test_scoreboard_settles_integer_handicap_with_plus_sign(rq, score, actual):
    p = {"p_home": .6, "p_draw": .25, "p_away": .15,
         "derivatives": {"handicap_1x2": {"line": rq, "p_home": .3, "p_draw": .4, "p_away": .3}}}
    s = score_one(p, {"home_goals": score[0], "away_goals": score[1]})
    assert s["handicap_outcome"] == actual
    assert s["handicap_hit"] == int(actual == 1)
    assert s["handicap_brier"] == round(sum((v - (i == actual))**2 for i, v in enumerate((.3, .4, .3))), 4)
    assert s["metric_schema"] == "multiclass_brier_sum-v2"


def test_risk_view_and_format_do_not_change_probabilities():
    p = [.31, .23, .46]
    original = p[:]
    v = handicap_view(p, -1)
    pred = {"derivatives": {"handicap_1x2": {"line": -1, "p_home": p[0], "p_draw": p[1], "p_away": p[2], "risk_view": v}}}
    text = format_handicap_1x2(pred)
    assert "让平=主队恰好赢1球" in text
    assert "覆盖77.0%" in text and "遗漏让平23.0%" in text
    assert p == original


def test_negative_fixed_odds_ev_is_a_difference_signal_not_value():
    with patch("engine.predictor.ensemble", return_value=(.31, .34, .35)), \
         patch("engine.predictor.shin_probs", return_value=(.25, .375, .375)):
        r = predict(payload(odds={"home": 3., "draw": 2., "away": 2.}), {"mc_min_score": 101})
    assert r["value"] == []
    c = r["probability_difference_candidates"]
    assert len(c) == 1 and c[0]["outcome"] == "home"
    assert c[0]["fixed_odds_expected_net"] == pytest.approx(-.07)
