"""Synthetic fixtures for production policy, immutable archives and fair evaluation."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from unittest.mock import MagicMock
import json
import pytest
from tests.test_jingcai_v211 import payload
from engine.jingcai_runtime import predict_jingcai,runtime_config
from engine.predictor import PredictError
from engine.jingcai_evaluation import evaluation_report,select_earliest
from engine.jingcai_archive import archive_prediction,verify_archive
from scripts.scoreboard import build_scoreboard


def stored_row(prediction_id='first',match_id=1,version=None,settled=True):
    # Deliberately synthetic database history, never written to forecast archives.
    now=datetime.now(timezone.utc);generated=now-timedelta(days=2)
    kickoff=now-timedelta(days=1);snapshot=generated-timedelta(hours=1)
    p=payload();r=predict_jingcai(p)
    p.update(snapshot_at=snapshot.isoformat(),kickoff_at=kickoff.isoformat())
    r['match']['kickoff_at']=p['kickoff_at']
    r['prediction_timing'].update(started_at=generated.isoformat(),generated_at=generated.isoformat(),
                                  decision_at=generated.isoformat(),snapshot_at=p['snapshot_at'])
    if version:r['model_version']=version
    return {'prediction_id':prediction_id,'match_id':match_id,'model_version':r['model_version'],
            'predicted_at':generated,'kickoff_at':kickoff,'league':'合成测试',
            'p_home':r['p_final_full'][0],'p_draw':r['p_final_full'][1],'p_away':r['p_final_full'][2],
            'payload':{'input':p,'result':r},'signals':r['signals'],'derivatives':r['derivatives'],
            'home_goals':2 if settled else None,'away_goals':0 if settled else None,
            'settled_at':now-timedelta(hours=1) if settled else None}


def later_row(row):
    newer=deepcopy(row);newer['prediction_id']='later';newer['predicted_at']+=timedelta(hours=1)
    newer['payload']['result']['prediction_timing']['generated_at']=newer['predicted_at'].isoformat()
    return newer


def test_api_and_batch_share_production_prediction_entry():
    from api.api.routes import predict as api_route
    from scripts import batch_predict_today
    assert api_route.predict_jingcai is batch_predict_today.predict_jingcai
    p=payload();r=predict_jingcai(p)
    assert r['calibrated'] is False
    assert r['calibration_policy']['production_calibration_enabled'] is False
    assert r['model_version'].startswith('v2.12+')


@pytest.mark.parametrize('config',[{'platt':{'home':(1,0)}},{'rho':float('nan')},{'kelly_fraction':-1}])
def test_unsafe_or_research_configs_are_rejected(config):
    with pytest.raises(PredictError):runtime_config(config)


def test_bad_environment_is_explicit_error(monkeypatch):
    monkeypatch.setenv('DIXON_COLES_RHO','bad')
    with pytest.raises(PredictError):runtime_config()


def test_archive_has_digest_and_before_kickoff_receipt(tmp_path):
    p=payload();r=predict_jingcai(p)
    receipt=archive_prediction(p,r,tmp_path)
    frozen,proof=verify_archive(tmp_path/receipt['forecast_id'])
    assert frozen['result']['p_final_full']==r['p_final_full']
    assert proof['status']=='saved_before_kickoff'
    assert proof['independent_source_provenance_verified'] is False
    second=archive_prediction(p,r,tmp_path)
    assert second['forecast_id']!=receipt['forecast_id']


def test_modified_forecast_bytes_fail_verification(tmp_path):
    p=payload();r=predict_jingcai(p);receipt=archive_prediction(p,r,tmp_path)
    folder=tmp_path/receipt['forecast_id']
    (folder/'forecast.json').write_bytes((folder/'forecast.json').read_bytes()+b' ')
    with pytest.raises(ValueError,match='bytes'):verify_archive(folder)


def test_late_durable_write_is_retained_but_not_eligible(tmp_path,monkeypatch):
    from engine import jingcai_archive
    p=payload();r=predict_jingcai(p)
    before=datetime.now(timezone.utc);late=datetime.fromisoformat(p['kickoff_at'])
    clock=MagicMock();clock.now.side_effect=[before,late]
    monkeypatch.setattr(jingcai_archive,'datetime',clock)
    receipt=archive_prediction(p,r,tmp_path)
    assert receipt['status']=='late_write'
    assert (tmp_path/receipt['forecast_id']/'forecast.json').is_file()
    with pytest.raises(ValueError,match='durably'):verify_archive(tmp_path/receipt['forecast_id'])


def test_earliest_forecast_wins_over_later_forecast():
    row=stored_row();later=later_row(row)
    selected,audit=select_earliest([later,row])
    assert selected[0]['prediction_id']=='first'
    assert audit['later_duplicate_predictions']==1


def test_pending_earliest_is_not_replaced_by_settled_later():
    row=stored_row(settled=False);later=later_row(row)
    later.update(home_goals=2,away_goals=0,settled_at=datetime.now(timezone.utc)-timedelta(hours=1))
    report=evaluation_report([row,later])
    assert report['n']==0
    assert report['audit']['pending_selected_predictions']==1


def test_versions_are_separate_for_same_match():
    rows=[stored_row(version='v2.12+variant1'),stored_row(version='v2.12+variant2')]
    report=evaluation_report(rows)
    assert len(report['by_version'])==2
    assert report['audit']['unique_matches']==1
    assert 'brier' not in report
    board=build_scoreboard(rows)
    assert len(board['by_version'])==2 and 'overall' not in board


@pytest.mark.parametrize('problem',['replay','missing_clock','rounded_db','wrong_version','future_generation'])
def test_ineligible_prediction_records_are_excluded(problem):
    row=stored_row();r=row['payload']['result']
    if problem=='replay':r['prediction_timing']['mode']='historical_replay'
    if problem=='missing_clock':r.pop('prediction_timing')
    if problem=='rounded_db':row['p_home']+=.001
    if problem=='wrong_version':row['model_version']='other'
    if problem=='future_generation':r['prediction_timing']['generated_at']=(datetime.now(timezone.utc)+timedelta(days=4)).isoformat()
    selected,audit=select_earliest([row])
    assert not selected and audit['excluded_rows']==1


def test_premature_settlement_remains_pending_without_replacing_forecast():
    row=stored_row();row['settled_at']=row['kickoff_at']-timedelta(hours=1)
    later=later_row(row);later['settled_at']=datetime.now(timezone.utc)-timedelta(hours=1)
    report=evaluation_report([row,later])
    assert report['audit']['invalid_selected_settlements']==1
    assert report['n']==0


def test_scoreboard_and_api_use_same_selection():
    row=stored_row();rows=[later_row(row),row]
    board=build_scoreboard(rows);report=evaluation_report(rows)
    v=row['model_version']
    assert board['audit']==report['audit']
    assert board['by_version'][v]['overall']['brier']==round(report['by_version'][v]['brier'],4)


def test_audit_keeps_corrupt_records_and_reports_them(tmp_path):
    from scripts.jingcai_archive_audit import audit_archive
    p=payload();r=predict_jingcai(p)
    first=archive_prediction(p,r,tmp_path);archive_prediction(p,r,tmp_path)
    folder=tmp_path/first['forecast_id']
    (folder/'forecast.json').write_bytes(b'invalid')
    report=audit_archive(tmp_path)
    assert report['eligible_local_archives']==1 and report['rejected_archives']==1
    assert folder.exists()


def test_replay_cannot_be_archived_as_a_new_prospective_forecast(tmp_path):
    p=payload();r=predict_jingcai(p);r['prediction_timing']['mode']='historical_replay'
    with pytest.raises(ValueError,match='historical'):archive_prediction(p,r,tmp_path)


def test_archive_failure_is_reported_by_api_without_claiming_storage(monkeypatch,tmp_path):
    from api.api.routes import predict as route
    monkeypatch.setattr(route,'get_conn',lambda:None)
    def fail(*a,**k):raise OSError('synthetic full disk')
    monkeypatch.setattr(route,'archive_prediction',fail)
    r=route.run_predict(payload())
    assert r['archive']['status']=='failed' and r['persistence']['status']=='unavailable'


def test_ambiguous_earliest_probabilities_cannot_be_cherry_picked():
    row=stored_row();other=deepcopy(row);other['prediction_id']='same-time-other'
    p=other['payload']['result']['p_final_full'];p[0]+=.001;p[1]-=.001
    other['p_home']=p[0];other['p_draw']=p[1]
    selected,audit=select_earliest([row,other])
    assert not selected
    assert audit['exclusion_reasons']['ambiguous_earliest_probabilities']==2


def test_conflicting_kickoffs_for_same_identity_are_quarantined():
    row=stored_row();other=later_row(row);other['kickoff_at']+=timedelta(hours=1)
    other['payload']['result']['match']['kickoff_at']=other['kickoff_at'].isoformat()
    selected,audit=select_earliest([row,other])
    assert not selected
    assert audit['exclusion_reasons']['conflicting_kickoff_identity']==2
