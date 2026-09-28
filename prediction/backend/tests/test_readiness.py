"""Pure offline data-coverage regression tests; no database or network."""
from app.services.readiness import coverage_report


def test_missing_and_failed_never_count_as_available():
    result = coverage_report({"modules": {"analysis": {"status": "failed", "reason": "HTTP 404"},
                                                   "asia": {"status": "success"}},
                              "sporttery_odds": [], "kickoff_at": None})
    assert result["status"] == "incomplete"
    assert not result["module_fields_present"]
    assert len(result["missing"]) == 8


def test_real_flat_fields_appear_without_claiming_prediction():
    result = coverage_report({"modules": {"asia": {"盘口": "一球/球半"}},
                              "sporttery_odds": [{"market": "wdl", "home_odds": "2.2", "draw_odds": "3.3", "away_odds": "3.1"}],
                              "kickoff_at": "2026-09-21T12:00:00+00:00"})
    assert result["verified_sporttery_rows"] == 1
    assert [x["key"] for x in result["module_fields_present"]] == ["asia"]
    assert result["status"] == "incomplete"
    assert "未运行AI预测" in result["notice"]


def test_unverified_nested_objects_do_not_claim_present():
    result = coverage_report({"modules": {"corners": {"raw_payload": {"unknown": [1, 2]}}}})
    assert not result["module_fields_present"]
