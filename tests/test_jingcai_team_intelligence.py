"""合成情报契约：不把示例当真实球队/外援数据，不证明准确率改善。"""
from copy import deepcopy
import hashlib
from datetime import timedelta

import pytest

from engine.jingcai_team_intelligence import build_intelligence, digest, SCHEMA
from engine.jingcai_predictor import predict, PredictError
from scripts.import_jingcai_team_intelligence import import_intelligence
from scripts.jingcai_format import format_team_intelligence
from engine.jingcai_team_features import extract_rows
from engine.jingcai_ledger import input_digest
from test_jingcai_review_ledger import ledger_row
from test_jingcai_inputs_grid import payload, CONFIG, ASOF

RAW=b'synthetic registration, player stats and lineup fixture; not real data'


def intelligence(p):
    match={k:p[k] for k in ('home','away','competition','kickoff_at')};match['season']='2029/30'
    e={'id':'syn','source':'synthetic_contract_fixture','url':'https://example.com/synthetic',
       'sha256':hashlib.sha256(RAW).hexdigest(),'collected_at':p['snapshot_at'],
       'available_at':p['snapshot_at']}
    teams={}
    for side in ('home','away'):
        players=[]
        for i in range(12):
            players.append({'player_id':str(i),'name':f'合成球员{i}',
                'position':'GK' if i==0 else 'FWD' if i>=9 else 'MID',
                'foreign_status':'foreign' if i in (9,10,11) else 'domestic',
                'registration_evidence_id':'syn','availability':'available',
                'availability_evidence_id':'syn',
                'registration_scope':{'team':p[side],'competition':p['competition'],'season':'2029/30'},
                'season_minutes':720 if i<11 else 80,'season_goals':4 if i in (9,10) else 0,
                'season_assists':2 if i==9 else 0,'season_stats_evidence_id':'syn',
                'reference_minutes_per_match':90 if i<11 else 0})
        teams[side]={'team':p[side],'player_id_namespace':'synthetic','roster_complete':True,
            'reference':{'history_sha256':digest(p[side+'_recent']),
                         'match_count':len(p[side+'_recent']),'evidence_id':'syn'},
            'players':players,'lineup':{'status':'confirmed','evidence_id':'syn',
               'minute_allocation_complete':True,
               'starting_ids':[str(i) for i in range(11)],'bench_ids':['11'],
               'anticipated_minutes':{str(i):90 if i<11 else 0 for i in range(12)}}}
    return {'schema':SCHEMA,'match':match,'evidence':[e],'teams':teams,
            'fixture_kind':'synthetic_not_real_foreign_player_information'}


def bound():
    p=payload();p['team_intelligence']=intelligence(p);return p


def test_missing_is_unknown_not_zero_foreign_players():
    r=build_intelligence(payload(),ASOF)
    assert r['status']=='missing' and not r['probability_effect_enabled']
    assert 'teams' not in r


def test_foreign_starters_bench_and_reference_features_are_separate():
    p=bound();before=deepcopy(p);r=build_intelligence(p,ASOF);h=r['teams']['home']
    assert p==before and h['foreign_starters_exact']==2
    assert h['observed_foreign_roster_n']==3 and h['lineup_status']=='confirmed'
    assert h['features']['foreign_reference_minutes_per_match']==180
    assert h['features']['foreign_anticipated_minutes']==180
    assert h['features']['foreign_minutes_delta']==0
    assert h['features']['starting_reference_continuity']==1
    assert r['provenance_status']=='caller_declared_evidence_not_source_authenticated'


def test_intelligence_does_not_change_probabilities_or_grade_or_hash_binding():
    p=bound();base=predict(payload(),CONFIG,asof=ASOF);new=predict(p,CONFIG,asof=ASOF)
    assert base['p_final_full']==new['p_final_full']
    assert base['score_matrix_full']==new['score_matrix_full']
    assert base['grade']==new['grade'] and base['completeness']==new['completeness']
    assert new['team_intelligence']['status']=='ok'
    assert base['input_sha256']!=new['input_sha256']


@pytest.mark.parametrize('kind',['schema','team','competition','season','kickoff','reference_hash','reference_count',
    'duplicate_player','unknown_starting_id','overlap','confirmed_10','bool_minutes','negative_goals',
    'over_minutes','unknown_evidence','foreign_scope','foreign_no_evidence','url_credentials'])
def test_invalid_identity_and_evidence_rejected(kind):
    p=bound();b=p['team_intelligence'];h=b['teams']['home'];x=h['players'][9]
    if kind=='schema':b['schema']='other'
    elif kind=='team':b['match']['home']='另一队'
    elif kind=='competition':b['match']['competition']='另一赛事'
    elif kind=='season':p['season']='2030/31'
    elif kind=='kickoff':b['match']['kickoff_at']=p['snapshot_at']
    elif kind=='reference_hash':h['reference']['history_sha256']='0'*64
    elif kind=='reference_count':h['reference']['match_count']=7
    elif kind=='duplicate_player':h['players'].append(deepcopy(x))
    elif kind=='unknown_starting_id':h['lineup']['starting_ids'][0]='unknown'
    elif kind=='overlap':h['lineup']['bench_ids']=['0']
    elif kind=='confirmed_10':h['lineup']['starting_ids'].pop()
    elif kind=='bool_minutes':x['reference_minutes_per_match']=True
    elif kind=='negative_goals':x['season_goals']=-1
    elif kind=='over_minutes':h['lineup']['anticipated_minutes']['11']=90
    elif kind=='unknown_evidence':h['reference']['evidence_id']='other'
    elif kind=='foreign_scope':x['registration_scope']['team']='转会前球队'
    elif kind=='foreign_no_evidence':x['registration_evidence_id']=None
    elif kind=='url_credentials':b['evidence'][0]['url']='https://user:pass@example.com/'
    with pytest.raises(PredictError):predict(p,CONFIG,asof=ASOF)


@pytest.mark.parametrize('location',['evidence','player','lineup','reference'])
def test_nested_future_timestamp_rejected(location):
    p=bound();b=p['team_intelligence'];h=b['teams']['home']
    target=b['evidence'][0] if location=='evidence' else h['players'][0] if location=='player' else h[location]
    target['available_at']=(ASOF+timedelta(days=1)).isoformat()
    with pytest.raises(PredictError,match='未来泄漏'):predict(p,CONFIG,asof=ASOF)


def test_unknown_registration_does_not_become_domestic():
    p=bound();p['team_intelligence']['teams']['home']['players'][9]['foreign_status']='unknown'
    h=build_intelligence(p,ASOF)['teams']['home']
    assert h['foreign_starters_exact'] is None and h['foreign_starters_known']==1
    assert h['foreign_starters_unknown']==1
    assert h['features']['foreign_reference_minutes_per_match'] is None


def test_expected_partial_lineup_and_missing_minutes_stay_partial():
    p=bound();line=p['team_intelligence']['teams']['home']['lineup']
    line['status']='expected';line['starting_ids']=line['starting_ids'][:5];line.pop('anticipated_minutes')
    line['minute_allocation_complete']=False
    h=build_intelligence(p,ASOF)['teams']['home']
    assert h['foreign_starters_exact'] is None and h['lineup_status']=='expected'
    assert h['features']['foreign_anticipated_minutes'] is None


def test_one_team_unknown_is_explicit_partial():
    p=bound();p['team_intelligence']['teams']['away']=None
    r=build_intelligence(p,ASOF)
    assert r['status']=='partial' and r['teams']['away'] is None


def test_confirmed_absence_share_and_replacement_are_observed_without_fixed_cut():
    p=bound();h=p['team_intelligence']['teams']['home'];x=h['players'][9]
    x['availability']='suspended';line=h['lineup']
    line['starting_ids'].remove('9');line['starting_ids'].append('11');line['bench_ids']=[]
    line['anticipated_minutes']['9']=0;line['anticipated_minutes']['11']=90
    h['replacements']=[{'absent_id':'9','replacement_id':'11','evidence_id':'syn'}]
    r=build_intelligence(p,ASOF);out=r['teams']['home']
    assert out['confirmed_unavailable'][0]['player_id']=='9'
    assert out['features']['unavailable_season_goal_share']==.5
    assert out['features']['unavailable_reference_minutes_observed']==90
    assert not r['probability_effect_enabled']


def test_conflicting_absence_and_lineup_is_not_automatically_resolved():
    p=bound();p['team_intelligence']['teams']['home']['players'][9]['availability']='injured'
    with pytest.raises(PredictError,match='冲突'):predict(p,CONFIG,asof=ASOF)


def test_partial_roster_cannot_claim_whole_team_goal_share():
    p=bound();h=p['team_intelligence']['teams']['home'];h['roster_complete']=False
    h['lineup']['minute_allocation_complete']=False
    out=build_intelligence(p,ASOF)['teams']['home']
    assert out['features']['unavailable_season_goal_share'] is None
    assert out['features']['foreign_reference_minutes_per_match'] is None


def test_unknown_availability_does_not_claim_zero_absence_goal_share():
    p=bound();p['team_intelligence']['teams']['home']['players'][9]['availability']='unknown'
    out=build_intelligence(p,ASOF)['teams']['home']
    assert out['unknown_or_doubtful_availability_n']==1
    assert out['features']['unavailable_season_goal_share'] is None


def test_partial_minute_allocation_does_not_claim_whole_foreign_minutes_delta():
    p=bound();line=p['team_intelligence']['teams']['home']['lineup']
    line['minute_allocation_complete']=False
    out=build_intelligence(p,ASOF)['teams']['home']
    assert out['features']['foreign_minutes_delta'] is None


def test_import_checks_actual_bytes_without_fabricating_times(tmp_path):
    p=payload();b=intelligence(p);raw=tmp_path/'source.bin';raw.write_bytes(RAW)
    before=deepcopy(p);enriched,report=import_intelligence(p,b,{'syn':str(raw)})
    assert p==before and report['evidence_byte_hashes_checked']==1
    assert enriched['snapshot_at']==p['snapshot_at']
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError,match='摘要'):import_intelligence(p,b,{'syn':str(raw)})


def feature_row():
    row=ledger_row();p=row['payload']['input'];r=row['payload']['result']
    p['home_recent']=[{'gf':2,'ga':1,'venue':'H'}]*8
    p['away_recent']=[{'gf':1,'ga':1,'venue':'A'}]*8
    p['team_intelligence']=intelligence(p);r['input_sha256']=input_digest(p)
    r['team_intelligence']=build_intelligence(p,ASOF)
    return row


def test_bound_feature_export_preserves_provenance_and_missingness():
    from datetime import datetime
    r=feature_row();report=extract_rows([r],asof=datetime.fromisoformat('2030-01-10T12:00:00+08:00'))
    assert len(report['feature_rows'])==1
    assert report['feature_rows'][0]['teams']['home']['foreign_starters_exact']==2
    assert report['production_effect_enabled'] is False


@pytest.mark.parametrize('kind',['stored_tamper','input_tamper','missing','wrong_project'])
def test_unbound_team_features_do_not_enter_export(kind):
    from datetime import datetime
    row=feature_row();r=row['payload']['result'];p=row['payload']['input']
    if kind=='stored_tamper':r['team_intelligence']['teams']['home']['foreign_starters_exact']=99
    elif kind=='input_tamper':p['team_intelligence']['teams']['home']['players'][9]['foreign_status']='domestic'
    elif kind=='missing':p.pop('team_intelligence');r['input_sha256']=input_digest(p)
    elif kind=='wrong_project':r['project_scope']='foreign'
    report=extract_rows([row],asof=datetime.fromisoformat('2030-01-10T12:00:00+08:00'))
    assert not report['feature_rows'] and sum(report['excluded'].values())==1


def test_report_distinguishes_missing_expected_and_confirmed():
    assert '未知' in format_team_intelligence({})[0]
    p=bound();out=build_intelligence(p,ASOF)
    assert '确认首发' in format_team_intelligence({'team_intelligence':out})[0]
    p['team_intelligence']['teams']['home']['lineup']['status']='expected'
    out=build_intelligence(p,ASOF)
    assert '预计首发' in format_team_intelligence({'team_intelligence':out})[0]


def test_invalid_evidence_reference_type_rejected_cleanly():
    p=bound();p['team_intelligence']['teams']['home']['reference']['evidence_id']=[]
    with pytest.raises(PredictError):predict(p,CONFIG,asof=ASOF)
