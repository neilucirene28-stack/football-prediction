"""Regression checks for prospective clocks, uncertainty and settlement arithmetic."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest
from unittest.mock import patch
from engine.predictor import predict, PredictError
from engine.poisson import match_probs
from engine.backtest import calibration_by_class
from api.api.persist import build_prediction_row
from scripts.scoreboard import score_one, aggregate
from scripts.jingcai_format import format_five_playtypes


def payload():
    now = datetime.now(timezone.utc)
    return {'home':'主队', 'away':'客队', 'competition':'测试',
            'kickoff_at':(now+timedelta(days=2)).isoformat(),
            'snapshot_at':(now-timedelta(hours=1)).isoformat(),
            'home_recent':[{'gf':2,'ga':1,'venue':'H'} for _ in range(8)],
            'away_recent':[{'gf':1,'ga':1,'venue':'A'} for _ in range(8)],
            'league_avg_goals':2.7, 'handicap_line':-1}


def test_full_distribution_and_summary_do_not_change_probabilities():
    r = predict(payload())
    assert match_probs(r['score_matrix_full']) == pytest.approx(r['p_final_full'], abs=1e-9)
    summary = r['derivatives']['score_summary']
    cells = sorted((p for row in r['score_matrix_full'] for p in row), reverse=True)
    assert summary['top5_probability'] == pytest.approx(sum(cells[:5]))
    assert summary['top5_probability'] + summary['outside_top5_probability'] == pytest.approx(1)
    assert summary['best_draw_score']['score'].split('-')[0] == summary['best_draw_score']['score'].split('-')[1]
    lines = format_five_playtypes(r)
    assert len(lines) == 7
    assert '平局排序' in lines[0] and '合计覆盖' in lines[2]
    assert sum(r['p_final_full']) == pytest.approx(1)


@pytest.mark.parametrize('case', ['future_snapshot', 'equal_kickoff', 'naive_asof'])
def test_time_guard(case):
    p = payload()
    asof = None
    if case == 'future_snapshot': p['snapshot_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    if case == 'equal_kickoff': p['snapshot_at'] = p['kickoff_at']
    if case == 'naive_asof': asof = datetime.now()
    with pytest.raises(PredictError): predict(p, asof=asof)


def test_replay_keeps_real_generation_clock_and_cannot_be_persisted():
    p = payload()
    p['snapshot_at'] = '2025-01-01T10:00:00+00:00'
    p['kickoff_at'] = '2025-01-01T15:00:00+00:00'
    r = predict(p, asof=datetime.fromisoformat('2025-01-01T11:00:00+00:00'))
    assert r['prediction_timing']['mode'] == 'historical_replay'
    assert datetime.fromisoformat(r['prediction_timing']['generated_at']).year >= 2026
    with pytest.raises(ValueError, match='replay'): build_prediction_row(1, p, r)


def test_finish_after_kickoff_is_rejected():
    p = payload()
    start = datetime.now(timezone.utc)
    finish = datetime.fromisoformat(p['kickoff_at'])
    with patch('engine.predictor.datetime') as clock:
        clock.fromisoformat.side_effect = datetime.fromisoformat
        clock.now.side_effect = [start, finish]
        with pytest.raises(PredictError, match='完成'): predict(p)


def test_persistence_uses_generation_clock_and_exact_vector():
    p = payload(); r = predict(p)
    row = build_prediction_row(1, p, r)
    assert row['predicted_at'] == r['prediction_timing']['generated_at']
    assert row['predicted_at'] != p['snapshot_at']
    assert [row[k] for k in ('p_home','p_draw','p_away')] == r['p_final_full']
    r['prediction_timing']['generated_at'] = p['kickoff_at']
    with pytest.raises(ValueError): build_prediction_row(1, p, r)


@pytest.mark.parametrize('line,hg,ag,outcome', [(-1,1,0,1),(-1,0,0,2),(1,0,1,1),(1,0,0,0)])
def test_handicap_sign_matches_home_goals_plus_line(line,hg,ag,outcome):
    pred = {'p_home':.5,'p_draw':.3,'p_away':.2, 'derivatives':{
        'handicap_1x2': {'line':line,'p_home':.2,'p_draw':.6,'p_away':.2}}}
    metrics = score_one(pred, {'home_goals':hg,'away_goals':ag})
    assert metrics['handicap_hit'] == int(outcome == 1)
    expected = sum((p-int(i==outcome))**2 for i,p in enumerate((.2,.6,.2)))/3
    assert metrics['handicap_brier'] == round(expected,4)
    assert 'top3_hit' not in metrics and 'top5_hit' not in metrics


def test_missing_scores_do_not_dilute_coverage_and_bias_keeps_sign():
    pred = {'p_home':.5,'p_draw':.3,'p_away':.2, 'derivatives':{'expected_goals':2.8}}
    a = score_one(pred, {'home_goals':1,'away_goals':1})
    bpred = deepcopy(pred)
    bpred['derivatives']['top_scores'] = [{'score':s} for s in ('1-0','1-1','0-1','2-0','0-2')]
    bpred['derivatives']['score_summary'] = {'top5_probability':.55}
    b = score_one(bpred, {'home_goals':1,'away_goals':1})
    report = aggregate([a,b])
    assert report['top5_hit_n'] == 1 and report['top5_hit'] == 1
    assert report['goals_bias'] == .8 and report['draw_actual'] == 1
    assert report['draw_brier'] == .49


def test_draw_and_away_calibration_are_separate():
    table = calibration_by_class([((.5,.3,.2),1), ((.4,.3,.3),2)])
    assert table['draw'][0] == {'bin':'0.3-0.4','n':2,'predicted':.3,'actual':.5}
    assert sum(r['n'] for r in table['away']) == 2


def test_legacy_prediction_cannot_borrow_snapshot_as_generation_time():
    p = payload(); r = predict(p)
    r.pop('prediction_timing')
    with pytest.raises(ValueError, match='timestamp'): build_prediction_row(1,p,r)


@pytest.mark.parametrize('field,value', [('odds',{'home':float('nan'),'draw':3,'away':2}),
                                        ('league_avg_goals',0),('handicap_line',-.5),
                                        ('kickoff_at','bad-date')])
def test_bad_inputs_are_explicit_prediction_errors(field,value):
    p=payload();p[field]=value
    with pytest.raises(PredictError): predict(p)


def test_recent_result_on_or_after_snapshot_date_is_rejected():
    p=payload();p['home_recent'][0]['date']=p['snapshot_at'][:10]
    with pytest.raises(PredictError,match='快照日'): predict(p)


def test_distinct_simultaneous_matches_count_separately_for_shadow_weights():
    from engine.adaptive_weights import accumulate, shadow_weights
    now=datetime.now(timezone.utc)
    _,n=accumulate([{'at':now,'model':.2,'market':.3} for _ in range(20)],now)
    assert n==20
    rows=[{'kickoff_at':now,'logloss_model':.5,'logloss_market':.6} for _ in range(20)]
    rows[0].pop('logloss_market')
    sh=shadow_weights(rows,now)
    assert sh['comparison_signals']==['model'] and sh['weights'] is None
    assert sh['excluded_signals']==['market']


@pytest.mark.parametrize('outcome',[0,1,2])
def test_rps_is_zero_for_perfect_prediction(outcome):
    from engine.backtest import ranked_probability_score
    assert ranked_probability_score(tuple(float(i==outcome) for i in range(3)),outcome)==0
    assert ranked_probability_score((1/3,1/3,1/3),outcome)>0


def test_result_cannot_be_persisted_under_another_fixture():
    p=payload();r=predict(p);p['home']='其他主队'
    with pytest.raises(ValueError,match='fixture'): build_prediction_row(1,p,r)


def test_collector_snapshot_records_completion_not_start(monkeypatch):
    from collector.collector.sources import titan007
    start=datetime.now(timezone.utc);ticks=iter(start+timedelta(seconds=i) for i in range(30))
    class Clock:
        @staticmethod
        def now(*a,**k):return next(ticks)
    monkeypatch.setattr(titan007,'datetime',Clock)
    source=titan007.Titan007Source(request_delay=0)
    monkeypatch.setattr(source,'_get',lambda url:'')
    monkeypatch.setattr(source,'parse_x12js',lambda x:{})
    monkeypatch.setattr(source,'parse_analysis',lambda *a,**k:{})
    monkeypatch.setattr(source,'_parse_odds_table',lambda *a,**k:[])
    r=source._build_match({'matchid':'mock','home_team':'主','away_team':'客','kickoff_at':(start+timedelta(days=2)).isoformat()})
    assert datetime.fromisoformat(r['snapshot_at']) > datetime.fromisoformat(r['raw']['collection_started_at'])
    assert r['snapshot_at']==r['raw']['collection_completed_at']


def test_database_insert_checks_server_clock_and_rejects_late_missing_row():
    from unittest.mock import MagicMock
    from api.api.persist import save_prediction
    p=payload();r=predict(p);conn=MagicMock()
    cur=conn.cursor.return_value.__enter__.return_value
    cur.rowcount=0;cur.fetchone.return_value=None
    with pytest.raises(ValueError,match='database after kickoff'):save_prediction(conn,1,p,r)
    assert 'clock_timestamp()' in cur.execute.call_args_list[0].args[0]
    conn.commit.assert_not_called()


def test_database_existing_idempotent_row_remains_noop():
    from unittest.mock import MagicMock
    from api.api.persist import save_prediction
    p=payload();r=predict(p);conn=MagicMock()
    cur=conn.cursor.return_value.__enter__.return_value
    cur.rowcount=0;cur.fetchone.return_value=('already-stored',)
    assert save_prediction(conn,1,p,r)
    conn.commit.assert_called_once()
