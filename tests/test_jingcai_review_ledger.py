"""Synthetic contract fixtures only; no real historical forecasts are created."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import MagicMock, patch

import pytest

from engine.jingcai_ledger import input_digest, validate_live_record
from engine.jingcai_review import build_review, load_review_rows
from engine.jingcai_calibration import load_form_only_config
from engine.jingcai_batch import with_official_handicap, handicap_output
from engine.predictor import predict, PredictError
from api.api.persist import build_prediction_row, save_prediction
from scripts.scoreboard import score_one, aggregate

ASOF = datetime.fromisoformat("2030-01-10T12:00:00+08:00")


def ledger_row(match_id=1, version="synthetic-v1", **changes):
    p = {"home": "合成主队", "away": "合成客队", "competition": "合成测试联赛",
         "kickoff_at": "2030-01-02T12:00:00+08:00",
         "snapshot_at": "2030-01-01T10:00:00+08:00"}
    r = {"status": "ok", "model": "jingcai", "model_version": version,
         "evaluation_mode": "live", "prediction_generated_at": "2030-01-01T10:06:00+08:00",
         "feature_cutoff_at": p["snapshot_at"], "evaluation_asof": "2030-01-01T10:05:00+08:00",
         "match": {k:p[k] for k in ("home", "away", "kickoff_at")},
         "input_sha256": input_digest(p),
         "p_home": .6, "p_draw": .25, "p_away": .15, "p_final_full": [.6,.25,.15],
         "signals": {"model": [.6,.25,.15], "market": [.5,.3,.2]},
         "signals_full": {"model": [.60001,.24999,.15], "market": [.5,.3,.2]},
         "derivatives": {}}
    row = {"fixture_kind": "synthetic_contract_not_a_real_forecast", "match_id": match_id,
           "prediction_id": f"synthetic-{match_id}-{version}", "model_version": version,
           "league": p["competition"], "kickoff_at": p["kickoff_at"],
           "predicted_at": r["prediction_generated_at"], "settled_at": "2030-01-02T15:00:00+08:00",
           "source": "synthetic_unit_fixture", "p_home": .6, "p_draw": .25, "p_away": .15,
           "home_goals": 2, "away_goals": 1, "payload": {"input": p, "result": r}}
    row.update(changes)
    return row


def live_payload():
    now = datetime.now(timezone.utc)
    return {"home": "合成主队", "away": "合成客队", "competition": "测试联赛",
            "kickoff_at": (now+timedelta(days=2)).isoformat(),
            "snapshot_at": (now-timedelta(seconds=1)).isoformat(),
            "home_recent": [{"gf":2,"ga":1,"venue":"H"}]*8,
            "away_recent": [{"gf":1,"ga":1,"venue":"A"}]*8,
            "odds": {"home":1.8,"draw":3.5,"away":4.5}, "ou_line":2.5}


def test_prediction_is_bound_to_original_input_and_late_save_rejected():
    p = live_payload(); r = predict(p, {"mc_min_score":101})
    assert r["input_sha256"] == input_digest(p)
    assert build_prediction_row(1,p,r)["predicted_at"] == r["prediction_generated_at"]
    changed=deepcopy(p); changed["home_recent"][0]["gf"]=9
    with pytest.raises(ValueError, match="摘要不匹配"):
        build_prediction_row(1,changed,r)
    with pytest.raises(ValueError, match="开球"):
        validate_live_record(p,r,now=datetime.fromisoformat(p["kickoff_at"])+timedelta(seconds=1))


def test_generation_time_cannot_be_fabricated_after_kickoff():
    row=ledger_row(); r=row["payload"]["result"];p=row["payload"]["input"]
    r["prediction_generated_at"] = p["kickoff_at"]
    with pytest.raises(ValueError, match="开球"):
        validate_live_record(p,r,now=ASOF)


def test_database_deadline_rejection_is_not_reported_as_saved():
    p=live_payload();r=predict(p,{"mc_min_score":101})
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[None,None]
    with pytest.raises(ValueError, match="未封存"):
        save_prediction(conn,1,p,r)
    assert "clock_timestamp()" in cur.execute.call_args_list[0].args[0]
    conn.commit.assert_not_called();conn.rollback.assert_called_once()


@pytest.mark.parametrize("same_input", [True,False])
def test_idempotent_collision_checks_original_snapshot(same_input):
    p=live_payload();r=predict(p,{"mc_min_score":101})
    stored={"input":deepcopy(p),"result":deepcopy(r)}
    if not same_input:
        stored["input"]["home_recent"][0]["gf"]=9
        stored["result"]["input_sha256"]=input_digest(stored["input"])
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[None,{"payload":stored}]
    if same_input:
        assert save_prediction(conn,1,p,r)
        conn.commit.assert_called_once()
    else:
        with pytest.raises(ValueError,match="幂等键命中不同"):
            save_prediction(conn,1,p,r)
        conn.commit.assert_not_called();conn.rollback.assert_called_once()


def test_prediction_api_exposes_unsaved_state_without_database_error_contents():
    from api.api.routes import predict as route
    p=live_payload()
    conn=MagicMock()
    with patch.object(route,"get_conn",return_value=conn), patch.object(route,"save_prediction",side_effect=RuntimeError("sensitive database details")):
        r=route.run_predict(p)
    assert r["persistence"] == {"status":"not_saved","reason":"write_failed","error_type":"RuntimeError"}
    assert "sensitive" not in json.dumps(r)
    conn.close.assert_called_once()


def test_review_versions_and_physical_matches_are_separate():
    rows=[ledger_row(i,v) for i in range(1,21) for v in ("v1","v2")]
    review=build_review(rows,asof=ASOF)
    assert review["n"] == 40 and review["unique_physical_matches"] == 20
    assert set(review["by_version"]) == {"v1","v2"}
    assert "shadow_weights" not in review
    for version in ("v1","v2"):
        sh=review["by_version"][version]["shadow_by_signal_cohort"]["model+market"]
        assert sh["n"] == 20 and sh["n_eff"] > 18 and sh["weights"] is not None


def test_multiple_forecasts_choose_earliest_once_independent_of_row_order():
    first=ledger_row();late=deepcopy(first)
    late["prediction_id"]="later-forecast";late["predicted_at"]="2030-01-01T11:00:00+08:00"
    late["payload"]["result"]["prediction_generated_at"] = late["predicted_at"]
    r1=build_review([late,first],asof=ASOF);r2=build_review([first,late],asof=ASOF)
    assert r1 == r2 and r1["n"] == 1
    assert r1["excluded"]["additional_forecast_same_match_version"] == 1


@pytest.mark.parametrize("kind", ["beidan", "replay", "unknown_version", "future_result", "post_kickoff_seal", "changed_input", "changed_stored_probability", "bad_score"])
def test_bad_or_other_project_records_excluded(kind):
    row=ledger_row();r=row["payload"]["result"]
    if kind=="beidan": r["model"]="beidan"
    elif kind=="replay": r["evaluation_mode"]="historical_replay"
    elif kind=="unknown_version": row["model_version"]="unknown"
    elif kind=="future_result": row["settled_at"]=(ASOF+timedelta(seconds=1)).isoformat()
    elif kind=="post_kickoff_seal": row["predicted_at"]="2030-01-02T12:01:00+08:00"
    elif kind=="changed_input": row["payload"]["input"]["extra"]="changed"
    elif kind=="changed_stored_probability": row["p_home"]=.7
    else: row["home_goals"]=-1
    review=build_review([row],asof=ASOF)
    assert review["status"] == "no_data" and sum(review["excluded"].values()) == 1


def test_future_result_cannot_affect_current_shadow_weights():
    rows=[ledger_row(i) for i in range(1,21)]
    future=ledger_row(999,settled_at=(ASOF+timedelta(seconds=1)).isoformat())
    current=build_review(rows,asof=ASOF)["by_version"]
    with_future=build_review(rows+[future],asof=ASOF)["by_version"]
    assert current == with_future


def test_missing_signals_do_not_get_artificially_favored_or_change_paired_n():
    rows=[ledger_row(i) for i in range(1,21)]
    for row in rows[:5]:
        row["payload"]["result"]["signals_full"]["market"]=None
    r=build_review(rows,asof=ASOF)["by_version"]["synthetic-v1"]
    assert r["paired_signals"]["model_vs_market"]["n"] == 15
    assert r["shadow_by_signal_cohort"]["model+market"]["n"] == 15


def test_legacy_timestamp_evidence_may_be_scored_but_not_learned():
    row=ledger_row();row["payload"]["result"].pop("input_sha256")
    r=build_review([row],asof=ASOF)["by_version"]["synthetic-v1"]
    assert r["metrics"]["n"] == 1 and r["not_eligible_for_shadow_learning"] == 1
    assert r["shadow_by_signal_cohort"] == {}


def test_scoreboard_full_precision_and_full_only_handicap_supported():
    p={"p_home":.6,"p_draw":.25,"p_away":.15,"p_final_full":[.60004,.24996,.15],
       "derivatives":{"handicap_1x2":{"line":-1,"p_home_full":.5,"p_draw_full":.2,"p_away_full":.3}}}
    r=score_one(p,{"home_goals":1,"away_goals":0})
    assert r["brier_full"] == pytest.approx((.60004-1)**2+.24996**2+.15**2,abs=1e-14)
    assert r["handicap_outcome"] == 1


@pytest.mark.parametrize("bad", [float("nan"),float("inf"),True])
def test_nonfinite_stored_probabilities_cannot_bypass_full_precision_validation(bad):
    row=ledger_row();row["p_home"]=bad;row["payload"]["result"]["p_home"]=bad
    review=build_review([row],asof=ASOF)
    assert review["n"] == 0


@pytest.mark.parametrize("ht", [(None,None),(0,None),(-1,0),(3,0),(True,0)])
def test_missing_or_invalid_halftime_is_not_scored_as_zero_zero(ht):
    p={"p_home":.6,"p_draw":.25,"p_away":.15,
       "derivatives":{"half_time":{"p_home":.4,"p_draw":.4,"p_away":.2},
                      "half_full_1x2_full":{a+b:1/9 for a in ("胜","平","负") for b in ("胜","平","负")}}}
    r=score_one(p,{"home_goals":2,"away_goals":1,"ht_home":ht[0],"ht_away":ht[1]})
    assert "half_time_brier" not in r and "half_full_brier" not in r
    assert any(reason.startswith("half_time:") for reason in r["unscored_playtypes"])


def test_all_playtype_scores_use_actual_settlement_and_separate_denominators():
    p=live_payload();p["handicap_line"]=-1;r=predict(p,{"mc_min_score":101})
    # 展示字段变化不能覆盖真实完整精度半场概率。
    r["derivatives"]["half_time"].update(p_home=0, p_draw=1, p_away=0)
    s=score_one(r,{"home_goals":2,"away_goals":1,"ht_home":0,"ht_away":0})
    totals=r["derivatives"]["total_goals_exact_full"]
    assert s["total_goals_brier_full"] == pytest.approx(sum((v-int(k=="3"))**2 for k,v in totals.items()))
    hf=r["derivatives"]["half_full_1x2_full"]
    assert s["half_full_brier_full"] == pytest.approx(sum((v-int(k=="平胜"))**2 for k,v in hf.items()))
    ht=r["derivatives"]["half_time_full"]
    assert s["half_time_brier_full"] == pytest.approx(sum((v-int(k=="draw"))**2 for k,v in ht.items()))
    assert s["half_time_brier_full"] > 0
    missing=score_one(r,{"home_goals":1,"away_goals":0})
    summary=aggregate([s,missing])
    assert summary["brier_n"]==2 and summary["total_goals_brier_n"]==2
    assert summary["half_full_brier_n"]==1 and summary["half_time_brier_n"]==1
    assert summary["unscored_playtypes"]["half_full:missing_result"]==1


def test_seven_plus_total_goals_bucket_and_missing_top_scores():
    totals={str(i):0 for i in range(7)};totals["7+"]=1
    p={"p_home":.6,"p_draw":.25,"p_away":.15,"derivatives":{"total_goals_exact_full":totals}}
    s=score_one(p,{"home_goals":5,"away_goals":3})
    assert s["total_goals_brier"]==0 and s["total_goals_hit"]==1
    assert "top3_hit" not in s and "top5_hit" not in s


def test_form_only_calibration_cannot_be_reused_with_elo_signal():
    p=live_payload();p["odds"]=None;p["elo"]={"home":1500,"away":1450}
    base=predict(p,{"mc_min_score":101})
    cfg={"mc_min_score":101,"platt":{k:[1,0] for k in ("home","draw","away")},
         "platt_provenance":provenance(base["base_model_version"],scope="form_only")}
    with pytest.raises(PredictError,match="范围不匹配"):
        predict(p,cfg)


def test_sql_uses_actual_ledger_and_parameterized_version():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchall.return_value=[]
    load_review_rows(conn,days=30,asof=ASOF,version="x' OR true --",limit=2000)
    sql,args=cur.execute.call_args.args
    assert "JOIN settlements" in sql and "backtest_results" not in sql
    assert "x' OR true" not in sql and "x' OR true --" in args


def test_api_summary_matches_same_ledger_review_and_closes_connection():
    from api.api.routes import backtest as route
    rows=[ledger_row()];conn=MagicMock()
    with patch.object(route,"get_conn",return_value=conn), patch.object(route,"load_review_rows",return_value=rows):
        r=route.summary()
    # Fixture is in future relative to real runtime; API must reject its result.
    assert r["scope"] == "jingcai_only" and r["status"] == "no_data"
    conn.close.assert_called_once()


def test_old_calibration_artifact_is_disabled_not_silently_applied():
    cfg,audit=load_form_only_config()
    assert cfg is None and audit["reason"] == "missing_version_and_cutoff_provenance"


def provenance(base, **changes):
    meta={"model":"jingcai","base_model_version":base,"scope":"market_fused",
          "training_results_available_before":"2026-01-01T00:00:00+08:00",
          "validation_results_available_before":"2026-02-01T00:00:00+08:00",
          "fitted_at":"2026-03-01T00:00:00+08:00","validation_kind":"chronological_holdout",
          "training_rows_sha256":"0"*64,"n_train":100,"n_validation":20}
    meta.update(changes);return meta


@pytest.mark.parametrize("changes", [None, {"base_model_version":"old"}, {"scope":"form_only"},
                                      {"fitted_at":"2035-01-01T00:00:00+08:00"},
                                      {"validation_kind":"random_5fold"}, {"training_rows_sha256":"missing"},
                                      {"validation_results_available_before":"2025-01-01T00:00:00+08:00"}])
def test_unqualified_platt_cannot_enter_jingcai_prediction(changes):
    p=live_payload();base=predict(p,{"mc_min_score":101})
    cfg={"mc_min_score":101,"platt":{k:[1,0] for k in ("home","draw","away")}}
    if changes is not None:cfg["platt_provenance"]=provenance(base["base_model_version"],**changes)
    with pytest.raises(PredictError, match="Platt"):
        predict(p,cfg)


def test_official_handicap_is_required_and_not_inferred_from_asian_market():
    p=live_payload();p["asian"]={"handicap":-1.5}
    r=predict(p,{"mc_min_score":101})
    assert handicap_output(r) is None
    official=with_official_handicap(p,{"rq":-1})
    r=predict(official,{"mc_min_score":101})
    assert handicap_output(r)["line"] == -1 and "handicap_line" not in p
    assert handicap_output(r)["p_draw_full"] == r["derivatives"]["handicap_1x2"]["p_draw_full"]
    with pytest.raises(ValueError, match="冲突"):
        with_official_handicap(dict(p,handicap_line=-2),{"rq":-1})


@pytest.mark.parametrize("rq", [None,-1])
def test_actual_batch_entry_exports_engine_handicap_and_unsaved_state(tmp_path,rq):
    from scripts import batch_predict_today as batch
    p=live_payload();p["asian"]={"handicap":-1.5}
    source_payload=dict(p,home_team=p["home"],away_team=p["away"],external_id="titan-synthetic")
    src=MagicMock();src._build_match.return_value=source_payload
    path=tmp_path/"fixtures.json";output=tmp_path/"output.json"
    path.write_text(json.dumps([{"matchid":1,"rq":rq}]))
    with patch.object(batch,"Titan007Source",return_value=src),patch.object(batch,"_db_conn",return_value=None),patch.object(batch.time,"sleep"):
        batch.main(str(path),str(output))
    row=json.loads(output.read_text())[0];r=row["prediction"]
    assert row["handicap_1x2"] == r["derivatives"]["handicap_1x2"]
    assert r["persistence"]["status"] == "not_saved"
    if rq is None: assert row["handicap_1x2"] is None
    else: assert row["handicap_1x2"]["line"] == rq
