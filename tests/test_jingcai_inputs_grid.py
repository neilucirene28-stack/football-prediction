"""Invalid-input, cutoff, recency and numerical-support regression checks."""
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

import pytest

from engine.jingcai_grid import score_grid
from engine.predictor import PredictError, predict
from engine.poisson import match_probs, pmf, score_matrix, half_full_1x2

ASOF = datetime.fromisoformat("2030-01-01T11:00:00+08:00")
CONFIG = {"mc_min_score": 101}


def payload(**changes):
    p = {"home": "主队", "away": "客队", "competition": "测试联赛",
         "kickoff_at": "2030-01-02T12:00:00+08:00",
         "snapshot_at": "2030-01-01T10:00:00+08:00",
         "home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
         "away_recent": [{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)],
         "odds": {"home": 1.8, "draw": 3.5, "away": 4.5},
         "handicap_line": -1, "ou_line": 2.5, "asian": {"handicap": -.5}}
    p.update(changes)
    return p


@pytest.mark.parametrize("bad", [-1, 0, 1, True, "bad", float("nan"), float("inf")])
def test_invalid_core_odds_rejected(bad):
    p = payload(); p["odds"]["home"] = bad
    with pytest.raises(PredictError, match="odds.home"):
        predict(p, CONFIG, asof=ASOF)


def test_partial_odds_are_not_silently_used():
    with pytest.raises(PredictError, match="odds.away"):
        predict(payload(odds={"home": 1.8, "draw": 3.5}), CONFIG, asof=ASOF)


@pytest.mark.parametrize("bad", [-1, True, 1.2, None])
def test_invalid_history_goals_rejected(bad):
    p = payload(); p["home_recent"][0]["gf"] = bad
    with pytest.raises(PredictError, match="非负整数"):
        predict(p, CONFIG, asof=ASOF)


@pytest.mark.parametrize("key,value", [("league_avg_goals", 0), ("league_avg_goals", float("nan")),
                                     ("ou_line", float("inf")), ("ou_line", -1),
                                     ("elo", {"home": float("nan"), "away": 1400}),
                                     ("injury", {"home_attack": -1}),
                                     ("asian", {"handicap": float("nan")})])
def test_invalid_model_inputs_rejected(key, value):
    with pytest.raises(PredictError, match=key):
        predict(payload(**{key: value}), CONFIG, asof=ASOF)


@pytest.mark.parametrize("key,value", [("decay", 0), ("decay", 1.1), ("ht_factor", 1),
                                     ("rho", float("nan")), ("letdraw_strength", 1.1),
                                     ("shrink_prior", -1), ("mc_n", True)])
def test_invalid_config_rejected_before_calculation(key, value):
    with pytest.raises(PredictError, match="config"):
        predict(payload(), dict(CONFIG, **{key: value}), asof=ASOF)


@pytest.mark.parametrize("bad", [datetime(2030, 1, 1), "", "2030-01-01T11:00:00+08:00"])
def test_asof_requires_aware_datetime(bad):
    with pytest.raises(PredictError, match="asof"):
        predict(payload(), CONFIG, asof=bad)


@pytest.mark.parametrize("bad", [None, 123, "bad", "2030-01-02T12:00:00"])
def test_bad_required_time_has_predict_error(bad):
    with pytest.raises(PredictError, match="kickoff_at"):
        predict(payload(kickoff_at=bad), CONFIG, asof=ASOF)


@pytest.mark.parametrize("placement", ["history", "odds", "features", "payload"])
def test_data_available_after_snapshot_rejected_even_before_asof(placement):
    p = payload(); future = "2030-01-01T10:30:00+08:00"
    if placement == "history": p["home_recent"][0]["available_at"] = future
    elif placement == "odds": p["odds"]["available_at"] = future
    elif placement == "features": p["feature_timestamps"] = {"elo": future}
    else: p["feature_as_of"] = future
    with pytest.raises(PredictError, match="快照截止"):
        predict(p, CONFIG, asof=ASOF)


@pytest.mark.parametrize("stamp", ["2030-01-02", "2030-01-01", "2030-01-01T02:00:00+08:00"])
def test_future_or_unfinished_same_day_history_rejected(stamp):
    p = payload(); p["home_recent"][0]["date"] = stamp
    with pytest.raises(PredictError, match="历史|completed_at"):
        predict(p, CONFIG, asof=ASOF)


def test_same_day_history_requires_finished_before_snapshot():
    p = payload(); row = p["home_recent"][0]
    row.update(date="2030-01-01", completed_at="2030-01-01T06:00:00+08:00")
    assert predict(p, CONFIG, asof=ASOF)["status"] == "ok"
    row["completed_at"] = "2030-01-01T10:01:00+08:00"
    with pytest.raises(PredictError, match="完赛时间"):
        predict(p, CONFIG, asof=ASOF)


def test_final_result_cannot_be_available_before_match_completed():
    p = payload()
    p["home_recent"][0].update(date="2029-12-31", completed_at="2029-12-31T20:00:00+08:00",
                               available_at="2029-12-31T19:00:00+08:00")
    with pytest.raises(PredictError, match="早于完赛"):
        predict(p, CONFIG, asof=ASOF)


def test_per_row_availability_evidence_is_recognized_without_global_timestamp():
    p = payload()
    for row in p["home_recent"]:
        row["available_at"] = "2029-12-31T20:00:00+08:00"
    r = predict(p, CONFIG, asof=ASOF)
    assert "home_recent" not in r["input_audit"]["features_without_available_at"]


def test_history_sorting_venue_alias_and_no_caller_mutation():
    p = payload()
    for idx, row in enumerate(p["home_recent"]):
        row.update(date=f"2029-12-{idx+1:02}", gf=idx, home_away=row.pop("venue"))
    original = deepcopy(p)
    ordered = deepcopy(p); ordered["home_recent"].reverse()
    r = predict(p, CONFIG, asof=ASOF); expected = predict(ordered, CONFIG, asof=ASOF)
    assert r["lambda_home_full"] == expected["lambda_home_full"]
    assert r["input_audit"]["history"]["home_recent"]["ordering"] == "date_descending"
    assert p == original


def test_undated_history_is_explicitly_unverified():
    r = predict(payload(), CONFIG, asof=ASOF)
    h = r["input_audit"]["history"]["home_recent"]
    assert h["missing_match_time"] == 8 and h["ordering"] == "input_order_unverified"
    assert "odds" in r["input_audit"]["features_without_available_at"]
    assert "unverified" in r["input_audit"]["status"]


def test_jingcai_h2h_uses_latest_three_not_oldest_three():
    h2h = [{"date": f"2029-12-{i:02}", "gf": 0 if i <= 3 else 2,
            "ga": 1} for i in range(1, 7)]
    r = predict(payload(h2h=h2h), CONFIG, asof=ASOF)
    assert r["lambda_notes"]["h2h_adjust"] == .03


@pytest.mark.parametrize("key,values", [("af_pred", [-.1, .5, .6]),
                                       ("af_pred", [float("nan"), .5, .5]),
                                       ("handicap_sp", [1.5, float("inf"), 3])])
def test_invalid_optional_signals_are_excluded_and_audited(key, values):
    r = predict(payload(**{key: values}), CONFIG, asof=ASOF)
    assert r["input_audit"]["excluded_optional_signals"][0]["field"] == key


@pytest.mark.parametrize("lh,la", [(1.5, 1.2), (4.635, 4.12), (.2, .1)])
def test_adaptive_grid_joint_tail_and_reference_distribution(lh, la):
    upper, tail = score_grid(lh, la)
    actual = 1 - sum(pmf(k, lh) for k in range(upper + 1)) * sum(pmf(k, la) for k in range(upper + 1))
    assert tail == pytest.approx(max(0, actual), abs=1e-15) and tail <= 1e-6
    m = score_matrix(lh, la, rho=-.13, max_goals=upper)
    reference = score_matrix(lh, la, rho=-.13, max_goals=30)
    assert match_probs(m) == pytest.approx(match_probs(reference), abs=1e-6)


def test_high_goal_match_half_full_marginal_exact_and_tail_bounded():
    with patch("engine.predictor.estimate_lambdas", return_value=(4.5, 4, {})):
        r = predict(payload(), CONFIG, asof=ASOF)
    assert r["score_grid"]["max_goals"] > 10
    assert r["score_grid"]["poisson_tail_mass_before_normalization"] <= 1e-6
    hf = r["derivatives"]["half_full_1x2_full"]
    marginals = [sum(p for label, p in hf.items() if label[-1] == outcome) for outcome in ("胜", "平", "负")]
    assert marginals == pytest.approx(r["p_final_full"], abs=1e-12)
    assert sum(hf.values()) == pytest.approx(1, abs=1e-12)


def test_impossible_dc_configuration_rejected_instead_of_clipped():
    with pytest.raises(PredictError, match="Dixon-Coles"):
        predict(payload(), dict(CONFIG, rho=-1), asof=ASOF)


def test_strict_half_full_retains_legacy_default():
    target = score_matrix(4.5, 4, rho=-.13)
    old = half_full_1x2(4.5, 4, rho=-.13, ft_matrix=target)
    strict = half_full_1x2(4.5, 4, rho=-.13, ft_matrix=target, strict_ft_grid=True)
    p = match_probs(target)
    def ft_marginals(hf):
        return [sum(v for label, v in hf.items() if label[-1] == k) for k in ("胜", "平", "负")]
    assert max(abs(x-y) for x,y in zip(ft_marginals(old), p)) > 1e-5
    assert ft_marginals(strict) == pytest.approx(p, abs=1e-12)
