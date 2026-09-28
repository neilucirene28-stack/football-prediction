"""No database, no credentials, no network: strict prediction gates."""
from datetime import datetime, timedelta, timezone
import pytest
from app.services.prediction import (PredictionError, analyze_insufficient_data,
                                     prepare_prediction_input, validate_model_result)

NOW = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)


def detail(**overrides):
    d = {"home_team": "主队", "away_team": "客队",
         "kickoff_at": (NOW + timedelta(hours=4)).isoformat(),
         "sporttery_odds": [{"market": "wdl", "home_odds": "2.4", "draw_odds": "3.1",
                              "away_odds": "2.9", "observed_at": NOW.isoformat()}]}
    d.update(overrides)
    return d


def test_historical_match_refused():
    with pytest.raises(PredictionError, match="historical"):
        prepare_prediction_input(detail(kickoff_at=(NOW-timedelta(hours=1)).isoformat()), now=NOW)


def test_missing_teams_refused():
    with pytest.raises(PredictionError, match="team identity"):
        prepare_prediction_input(detail(home_team=None), now=NOW)


def test_missing_kickoff_refused():
    with pytest.raises(PredictionError, match="kickoff"):
        prepare_prediction_input(detail(kickoff_at=None), now=NOW)


def test_bad_odds_refused():
    with pytest.raises(PredictionError, match="no valid"):
        prepare_prediction_input(detail(sporttery_odds=[{"market": "wdl", "home_odds": "nan", "draw_odds": "3.1", "away_odds": "2.9", "observed_at": NOW.isoformat()}]), now=NOW)


def test_future_odds_refused():
    row = detail()["sporttery_odds"][0].copy()
    row["observed_at"] = (NOW+timedelta(hours=1)).isoformat()
    with pytest.raises(PredictionError, match="no valid"):
        prepare_prediction_input(detail(sporttery_odds=[row]), now=NOW)


def test_valid_odds_alone_are_insufficient():
    with pytest.raises(PredictionError, match="independent"):
        prepare_prediction_input(detail(), now=NOW)


def test_no_external_call_and_no_prediction():
    result = analyze_insufficient_data(detail(), now=NOW)
    assert result["api_called"] is False and result["predictions"] is None


def test_enabling_call_fails_closed():
    with pytest.raises(PredictionError, match="not approved"):
        analyze_insufficient_data(detail(), enabled=True, now=NOW)


def test_model_cannot_claim_prediction():
    with pytest.raises(PredictionError):
        validate_model_result({"status": "predicted", "summary": "主胜"})
