"""Database-free checks of API persistence disclosure and settlement-backed review."""
from unittest.mock import MagicMock
import pytest
from tests.test_jingcai_v211 import payload

pytest.importorskip('fastapi')
from api.api.routes import predict as predict_route, backtest


@pytest.fixture(autouse=True)
def isolated_forecast_archive(tmp_path,monkeypatch):
    monkeypatch.setenv('JINGCAI_ARCHIVE_DIR',str(tmp_path))


def test_api_no_db_does_not_claim_saved(monkeypatch):
    monkeypatch.setattr(predict_route,'get_conn',lambda:None)
    r=predict_route.run_predict(payload())
    assert r['status']=='ok' and r['persistence']['status']=='unavailable'
    assert r['archive']['status']=='saved_before_kickoff'


def test_api_failed_save_is_disclosed_and_rolls_back(monkeypatch):
    conn=MagicMock()
    monkeypatch.setattr(predict_route,'get_conn',lambda:conn)
    def fail(*a,**k): raise RuntimeError('synthetic failure')
    monkeypatch.setattr(predict_route,'save_prediction',fail)
    r=predict_route.run_predict(payload())
    assert r['persistence']['status']=='failed'
    conn.rollback.assert_called_once();conn.close.assert_called_once()


def test_api_success_returns_prediction_id(monkeypatch):
    conn=MagicMock()
    monkeypatch.setattr(predict_route,'get_conn',lambda:conn)
    monkeypatch.setattr(predict_route,'save_prediction',lambda *a,**k:'mock-prediction-id')
    assert predict_route.run_predict(payload())['persistence']=={'status':'saved','prediction_id':'mock-prediction-id'}


def test_review_reads_settlements_and_all_class_calibration(monkeypatch):
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    from tests.test_jingcai_v212 import stored_row
    row=stored_row();row.update(home_goals=1,away_goals=1)
    cur.fetchall.return_value=[row]
    monkeypatch.setattr(backtest,'get_conn',lambda:conn)
    r=backtest.summary()
    assert 'JOIN settlements' in cur.execute.call_args[0][0]
    assert set(r['calibration_by_class'])=={'home','draw','away'}
    from engine.backtest import brier_score
    assert r['brier']==pytest.approx(brier_score(tuple(row['payload']['result']['p_final_full']),1))
