"""竞彩专用入口隔离用例，不运行北单预测或训练。"""
import copy
from datetime import datetime, timedelta, timezone

import pytest

import engine.jingcai_predictor as entry
from engine.predictor import predict as core_predict
from engine.jingcai_ledger import validate_live_record
from engine.jingcai_review import load_review_rows


def payload():
    now = datetime.now(timezone.utc)
    return {"home":"竞彩测试主队", "away":"竞彩测试客队", "competition":"测试联赛",
            "snapshot_at":(now-timedelta(seconds=1)).isoformat(),
            "kickoff_at":(now+timedelta(days=1)).isoformat(),
            "home_recent":[{"gf":2,"ga":1,"venue":"H"} for _ in range(8)],
            "away_recent":[{"gf":1,"ga":1,"venue":"A"} for _ in range(8)],
            "league_avg_goals":2.7, "handicap_line":-1,
            "odds":{"home":2.1,"draw":3.4,"away":3.6}}


@pytest.mark.parametrize("where,key,value", [
    ("argument", "model", "beidan"),
    ("payload", "model", "beidan"), ("payload", "project", "beidan"),
    ("payload", "project_scope", "beidan"), ("payload", "beidan", {}),
    ("config", "model", "beidan"), ("config", "project", "beidan"),
    ("config", "project_scope", "beidan"), ("config", "beidan_calibration", {}),
])
def test_other_project_rejected_before_engine_call(monkeypatch, where, key, value):
    def forbidden(*args, **kwargs):
        pytest.fail("不应启动任何预测流程")
    monkeypatch.setattr(entry, "_core_predict", forbidden)
    p=payload();cfg={"mc_min_score":101};kw={}
    if where=="argument":kw[key]=value
    elif where=="payload":p[key]=value
    else:cfg[key]=value
    with pytest.raises(entry.PredictError):entry.predict(p,cfg,**kw)


def test_jingcai_entry_has_scoped_version_and_no_beidan_output(monkeypatch):
    import engine.predictor as core
    def forbidden(*args, **kwargs):
        pytest.fail("竞彩不应调用北单校准")
    monkeypatch.setattr(core, "apply_beidan_calibration", forbidden)
    p=payload();cfg={"mc_min_score":101};p_before=copy.deepcopy(p);cfg_before=dict(cfg)
    result=entry.predict(p,cfg)
    original=core_predict(p,cfg)
    assert result["model"]==result["project_scope"]=="jingcai"
    assert "beidan" not in result
    assert result["model_version"]!=original["model_version"]
    assert result["base_model_version"]!=original["base_model_version"]
    assert result["p_final_full"]==original["p_final_full"]
    assert result["derivatives"]["handicap_1x2"]==original["derivatives"]["handicap_1x2"]
    assert p==p_before and cfg==cfg_before


def test_insufficient_data_still_identifies_jingcai_only():
    p=payload();p["home_recent"]=[];p["away_recent"]=[];p["odds"]=None
    result=entry.predict(p,{"mc_min_score":101})
    assert result["status"]=="insufficient_data"
    assert result["project_scope"]=="jingcai" and "beidan" not in result


def test_api_rejects_payload_selecting_other_project(monkeypatch):
    from fastapi import HTTPException
    from api.api.routes import predict as route
    def forbidden():
        pytest.fail("不应打开数据库")
    monkeypatch.setattr(route,"get_conn",forbidden)
    p=payload();p["model"]="beidan"
    with pytest.raises(HTTPException) as exc:route.run_predict(p)
    assert exc.value.status_code==422


@pytest.mark.parametrize("scope_source", ["legacy_dual_entry", "jingcai_entry"])
def test_calibration_base_version_must_match_jingcai_entry(scope_source):
    p=payload();cfg={"mc_min_score":101}
    base=core_predict(p,cfg) if scope_source=="legacy_dual_entry" else entry.predict(p,cfg)
    before=datetime.now(timezone.utc)-timedelta(days=3)
    cfg.update(platt={k:[1,0] for k in ("home","draw","away")},platt_provenance={
        "model":"jingcai", "base_model_version":base["base_model_version"],
        "scope":"market_fused", "training_results_available_before":before.isoformat(),
        "validation_results_available_before":(before+timedelta(days=1)).isoformat(),
        "fitted_at":(before+timedelta(days=2)).isoformat(), "validation_kind":"chronological_holdout",
        "training_rows_sha256":"0"*64, "n_train":100, "n_validation":20})
    if scope_source=="legacy_dual_entry":
        with pytest.raises(entry.PredictError,match="版本不匹配"):entry.predict(p,cfg)
    else:
        result=entry.predict(p,cfg)
        assert result["calibration_audit"]["base_model_version"]==base["base_model_version"]


@pytest.mark.parametrize("field", ["input_scope", "result_scope"])
def test_ledger_rejects_other_project_scope(field):
    p=payload();r=entry.predict(p,{"mc_min_score":101})
    if field=="input_scope":p["project_scope"]="beidan"
    else:r["project_scope"]="beidan"
    with pytest.raises(ValueError,match="其他项目"):validate_live_record(p,r)


def test_review_query_filters_jingcai_before_record_limit():
    from unittest.mock import MagicMock
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchall.return_value=[]
    load_review_rows(conn,days=30,asof=datetime.now(timezone.utc),limit=2000)
    sql,_=cur.execute.call_args.args
    assert "p.payload->'result'->>'model' = 'jingcai'" in sql
    assert "COALESCE(p.payload->'result'->>'project_scope', 'jingcai') = 'jingcai'" in sql
    assert sql.index("'jingcai'") < sql.index("LIMIT")
