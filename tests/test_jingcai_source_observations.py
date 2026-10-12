"""合成契约测试，不代表真实伤停、首发、外援身份或模型准确率提升。"""
from copy import deepcopy
from datetime import timedelta, datetime
from io import BytesIO
from urllib.error import HTTPError
import hashlib
import json
from unittest.mock import Mock

import pytest

from engine.jingcai_observations import SCHEMA, build_observations, match_identity, merge_same_source
from engine.jingcai_predictor import predict, PredictError
from engine.jingcai_ledger import input_digest
from engine.jingcai_team_features import extract_rows
from collector import jingcai_intelligence as c
from scripts.import_jingcai_observations import enrich, import_raw, verify_packets
from scripts.jingcai_format import format_team_intelligence
from test_jingcai_inputs_grid import payload, ASOF, CONFIG
from test_jingcai_review_ledger import ledger_row

RAW = b'synthetic source fixture only'


def meta(source='espn'):
    host = c.SOURCE_HOSTS[source][0]
    return {'source': source, 'url': 'https://' + host + '/synthetic',
            'sha256': hashlib.sha256(RAW).hexdigest(), 'http_status': 200, 'status': 'ok',
            'available_at': payload()['snapshot_at'], 'collected_at': payload()['snapshot_at']}


def packet(p=None, source='espn'):
    p = p or payload(season='2029/30')
    match = match_identity(p); teams = {}
    for side, tid in (('home', '1'), ('away', '2')):
        players = []
        for i in range(12):
            x = c._player(str(i), f'合成球员{i}', 'GK' if i == 0 else 'MID')
            x.update(availability='available', availability_evidence_id='http',
                     season_minutes=720, season_goals=1, season_assists=1, season_yellow=2, season_red=0,
                     season_stats_evidence_id='http', season_stats_scope={'competition': p['competition'], 'season': p['season']},
                     anticipated_minutes=90 if i < 11 else 0, anticipated_minutes_evidence_id='http',
                     registration={'category': 'foreign' if i >= 9 else 'domestic', 'counts_toward_foreign_quota': i >= 9,
                                   'evidence_id': 'http', 'rule_version': 'synthetic-2030',
                                   'scope': {'team': p[side], 'competition': p['competition'], 'season': p['season']}})
            players.append(x)
        teams[side] = {'team_id': tid, 'player_id_namespace': source, 'roster_complete': True, 'players': players,
                       'lineup': {'status': 'confirmed', 'starting_ids': [str(i) for i in range(11)], 'bench_ids': ['11'], 'evidence_id': 'http'}}
    return c._packet(meta(source), {'event_id': '7', 'home_id': '1', 'away_id': '2'}, match, teams)


def bound():
    p = payload(season='2029/30')
    p['team_observations'] = {'schema': SCHEMA, 'project': 'jingcai', 'match': match_identity(p), 'packets': [packet(p)]}
    return p


def result(p):
    return build_observations(p, datetime.fromisoformat(p['snapshot_at']))


def test_model_output_and_features_without_unvalidated_probability_changes():
    p = bound(); old = deepcopy(p); base = predict(payload(season='2029/30'), CONFIG, asof=ASOF)
    new = predict(p, CONFIG, asof=ASOF)
    assert p == old
    assert new['p_final_full'] == base['p_final_full']
    assert new['score_matrix_full'] == base['score_matrix_full']
    assert new['input_sha256'] != base['input_sha256']
    h = new['team_observations']['selected_by_side']['home']
    assert h['features']['foreign_starters_exact'] == 2
    assert h['features']['season_yellow_total'] == 24
    assert h['features']['yellow_per_player_90'] == .25
    assert h['features']['foreign_anticipated_minutes'] == 180
    assert not new['team_observations']['probability_effect_enabled']
    assert 'espn' in format_team_intelligence(new)[0]


def test_missing_not_zero_and_partial_roster_preserves_observed_stats():
    assert result(payload())['status'] == 'missing'
    p = bound(); h = p['team_observations']['packets'][0]['teams']['home']; h['roster_complete'] = False
    f = result(p)['selected_by_side']['home']['features']
    assert f['season_yellow_total'] is None and f['unavailable_season_goal_share'] is None
    assert f['observed_season_yellow_sum'] == 24


def test_unknown_registration_is_not_inferred_from_nationality():
    p = bound(); x = p['team_observations']['packets'][0]['teams']['home']['players'][9]
    x['registration'] = None; x['nationality'] = 'Brazil'
    assert result(p)['selected_by_side']['home']['features']['foreign_starters_exact'] is None


@pytest.mark.parametrize('kind', ['project', 'match', 'season', 'domain', 'namespace', 'teamid', 'duplicate', 'future',
                                 'lineup10', 'lineup_absent', 'reg_scope', 'stats_scope', 'negative', 'bool', 'key_url', 'blocked_players', 'minutes'])
def test_invalid_or_leaked_records_rejected(kind):
    p = bound(); block = p['team_observations']; q = block['packets'][0]; h = q['teams']['home']; x = h['players'][0]
    if kind == 'project': block['project'] = 'beidan'
    elif kind == 'match': q['match']['home'] = '别的球队'
    elif kind == 'season': q['match']['season'] = '2031'
    elif kind == 'domain': q['evidence'][0]['url'] = 'https://espn.com.evil.test/'
    elif kind == 'namespace': h['player_id_namespace'] = 'other'
    elif kind == 'teamid': h['team_id'] = '99'
    elif kind == 'duplicate': h['players'].append(deepcopy(x))
    elif kind == 'future': x['available_at'] = (ASOF + timedelta(days=1)).isoformat()
    elif kind == 'lineup10': h['lineup']['starting_ids'].pop()
    elif kind == 'lineup_absent': x['availability'] = 'injured'; x['anticipated_minutes'] = 0
    elif kind == 'reg_scope': x['registration']['scope']['season'] = '2031'
    elif kind == 'stats_scope': x['season_stats_scope']['competition'] = '别的赛事'
    elif kind == 'negative': x['season_red'] = -1
    elif kind == 'bool': x['season_yellow'] = True
    elif kind == 'key_url': q['evidence'][0]['url'] += '?api_key=secret'
    elif kind == 'blocked_players': q['status'] = 'blocked'
    elif kind == 'minutes': h['players'][11]['anticipated_minutes'] = 90; h['roster_complete'] = False
    with pytest.raises(PredictError): predict(p, CONFIG, asof=ASOF)


def test_source_priority_no_cross_namespace_merge_and_conflict_quarantine():
    p = bound(); official = packet(p, 'sporttery'); official['teams']['away'] = None
    official['teams']['home']['lineup'] = None
    p['team_observations']['packets'].append(official)
    r = result(p)
    assert r['selected_by_side']['home']['source'] == 'sporttery'
    assert r['selected_by_side']['away']['source'] == 'espn'
    assert r['selected_by_dimension']['home']['availability']['source'] == 'sporttery'
    assert r['selected_by_dimension']['home']['lineup']['source'] == 'espn'
    another = deepcopy(official); another['reason'] = 'different_capture'
    p['team_observations']['packets'].append(another)
    r = result(p)
    assert r['selected_by_side']['home']['source'] == 'espn' and len(r['conflicts']) == 1


def test_explicit_same_source_merge_by_id_and_evidence_rename():
    p = bound(); a = p['team_observations']['packets'][0]; b = deepcopy(a)
    for side in ('home', 'away'):
        a['teams'][side]['roster_complete'] = b['teams'][side]['roster_complete'] = False
        a['teams'][side]['players'][11]['availability'] = 'unknown'
        b['teams'][side]['lineup'] = None
        b['teams'][side]['players'] = [b['teams'][side]['players'][11]]
        for x in a['teams'][side]['players']:
            for k in c._player('0','x'):
                if k.startswith('season_'): x[k] = None
    merged = merge_same_source([a, b]); p['team_observations']['packets'] = merged
    r = result(p)
    assert r['status'] == 'ok' and len(merged[0]['evidence']) == 2
    assert merged[0]['teams']['home']['players'][11]['season_minutes'] == 720
    assert len(merged[0]['merged_packet_sha256']) == 2


@pytest.mark.parametrize('kind', ['stats', 'availability', 'namespace', 'fixture', 'complete_roster'])
def test_merge_never_resolves_conflicts_silently(kind):
    a = packet(); b = deepcopy(a); x = b['teams']['home']['players'][11]
    if kind == 'stats': x['season_minutes'] += 1
    elif kind == 'availability': x['availability'] = 'injured'
    elif kind == 'namespace': b['teams']['home']['player_id_namespace'] = 'different'
    elif kind == 'fixture': b['binding']['home_id'] = '99'
    elif kind == 'complete_roster': b['teams']['home']['players'].pop()
    with pytest.raises(ValueError): merge_same_source([a,b])


def espn_data(p):
    return {'header': {'competitions': [{'id': '7', 'date': p['kickoff_at'], 'status': {'type': {'state': 'pre'}},
             'competitors': [{'homeAway': side, 'team': {'id': tid}} for side, tid in (('home','1'),('away','2'))]}]},
            'rosters': [{'team': {'id': '1'}, 'roster': [{'athlete': {'id': str(i), 'displayName': '合成'}, 'position': {'abbreviation':'M'}, 'starter': i < 11} for i in range(12)]}]}


def test_espn_native_ids_pre_only_and_athlete_ids_kept():
    p = payload(season='2029/30'); data = espn_data(p); b = packet(p)['binding']
    q = c.espn_summary(data, meta(), b, match_identity(p))
    assert q['teams']['home']['players'][0]['player_id'] == '0'
    assert q['teams']['home']['lineup']['status'] == 'confirmed'
    assert not q['teams']['home']['roster_complete']
    with pytest.raises(ValueError, match='event ID'): c.espn_summary(data, meta(), {**b,'event_id':'wrong-okooo-mid'}, match_identity(p))
    data['header']['competitions'][0]['status']['type']['state'] = 'post'
    assert c.espn_summary(data, meta(), b, match_identity(p))['status'] == 'unsupported'


@pytest.mark.parametrize('raw,expected', [('25%','doubtful'),('50%','doubtful'),('75%','doubtful'),('Ruled Out','injured'),('100%','available'),('','unknown')])
def test_pi_percentages_are_status_not_probabilities(raw, expected):
    assert c.availability('', '', raw) == expected


def test_fotmob_groups_expand_members_skip_coach():
    data = {'squad': {'squad': [{'title':'keepers','members':[{'id':1,'name':'合成GK'}]}, {'title':'coach','members':[{'id':9,'name':'教练'}]}]}}
    rows = c.fotmob_roster(data)
    assert len(rows) == 1 and rows[0]['player_id'] == '1' and rows[0]['position'] == 'GK'
    with pytest.raises(ValueError, match='授权'): c.fetch('https://fotmob.com/', 'fotmob_authorized', 'unused')


def test_pi_mobile_titles_removed_native_ids_bind_without_name_guessing():
    p = payload(season='2029/30'); b = packet(p)['binding']; m = meta('premierinjuries')
    page = '<table class="injury-table injury-table-full injury-table-epl"><tr class="heading"><td><div class="injury-team">合成队</div></td></tr><tr class="player-row team_1">'
    for label,value in [('Player','合成球员'),('Reason','Thigh Injury'),('Further Detail','Oct 08: update'),('Potential Return','21/11/2030'),('Condition','Late Fitness Test'),('Status','50%')]:
        page += '<td><div class="mob-title">'+label+'</div>'+value+'</td>'
    page += '<td><a data-id="999" data-type="player">TRACK</a></td></tr></table>'
    q = c.premierinjuries_table(page,m,b,match_identity(p),[])
    x = q['teams']['home']['players'][0]
    assert x['player_id'] == '999' and x['name'] == '合成球员' and x['availability'] == 'doubtful'
    assert x['raw_detail'] == 'Oct 08: update' and 'published_at' not in x
    assert not q['unbound_rows']


def test_sporttery_fixture_does_not_turn_starts_count_into_match_lineup(monkeypatch):
    html = '<table><tr>' + ''.join('<th>'+h+'</th>' for h in ['球队','球员','位置','状态','首发','替补','进球','助攻']) + '</tr><tr>' + ''.join('<td>'+v+'</td>' for v in ['合成队','合成球员','前锋','出战成疑','20','5','10','3']) + '</tr></table>'
    parsed = c.sporttery_table(html); assert parsed['rows'][0]['availability'] == 'doubtful'
    p = payload(season='2029/30'); m = meta('sporttery'); b = packet(p)['binding']
    mapping = [{'team_name':'合成队','player_name':'合成球员','side':'home','team_id':'1','source':'sporttery','player_id':'999','mapping_evidence_sha256':m['sha256']}]
    q = c.bind_sporttery_rows(parsed['rows'],m,b,match_identity(p),mapping)
    assert q['teams']['home']['lineup'] is None
    assert q['teams']['home']['players'][0]['season_goals'] is None
    assert c.bind_sporttery_rows(parsed['rows'],m,b,match_identity(p),[])['status'] == 'missing'
    assert c.sporttery_table('<html>安全拦截</html>')['status'] == 'unsupported'
    monkeypatch.setattr(c, 'fetch', lambda *args, **kwargs: (html.encode(), m))
    assert c.collect('sporttery', b, match_identity(p), 'unused', player_bindings=mapping)['teams']['home']['players'][0]['player_id'] == '999'


def test_api_scope_paging_uncertainty_and_raw_cards():
    p = payload(season='2029/30'); b = {**packet(p)['binding'],'competition_id':'3','season_id':'2029'}
    d = {'parameters':{'fixture':'7'},'response':[{'team':{'id':1},'fixture':{'id':7},'player':{'id':8,'name':'合成','type':'Questionable','reason':'Knock'}}]}
    q = c.api_football(d,meta('api_football'),b,match_identity(p),'injuries')
    assert q['teams']['home']['players'][0]['availability'] == 'doubtful'
    d = {'parameters':{'team':'1','league':'3','season':'2029'}, 'paging':{'total':1},
         'response':[{'player':{'id':8,'name':'合成','nationality':'Brazil'},'statistics':[{'team':{'id':1},'league':{'id':3,'season':2029},'games':{'minutes':800},'goals':{'total':4,'assists':None},'cards':{'yellow':5,'red':1,'yellowred':2}}]}]}
    q = c.api_football(d,meta('api_football'),b,match_identity(p),'players')
    x = q['teams']['home']['players'][0]
    assert x['season_red'] == 1 and x['registration']['category'] == 'unknown'
    d['paging']['total'] = 2
    with pytest.raises(ValueError, match='多页'): c.api_football(d,meta('api_football'),b,match_identity(p),'players')


def test_sportmonks_confirmation_flag_is_not_presence_of_lineup():
    p = payload(season='2029/30'); b = {**packet(p)['binding'],'competition_id':'3','season_id':'4'}
    d = {'data':{'id':7,'league_id':3,'season_id':4,'starting_at_timestamp':int(datetime.fromisoformat(p['kickoff_at']).timestamp()),'state_id':1,
         'participants':[{'id':int(tid),'meta':{'location':side}} for side,tid in [('home','1'),('away','2')]],
         'lineups':[{'fixture_id':7,'team_id':1,'player_id':i+1,'player_name':'合成','position_id':26,'type_id':11} for i in range(11)], 'metadata':[]}}
    assert c.sportmonks_fixture(d,meta('sportmonks'),b,match_identity(p))['teams']['home']['lineup'] is None
    d['data']['metadata'] = [{'type_id':572,'metadatable_id':7,'values':{'confirmed':False}}]
    assert c.sportmonks_fixture(d,meta('sportmonks'),b,match_identity(p))['teams']['home']['lineup']['status'] == 'expected'
    d['data']['metadata'][0]['values']['confirmed'] = True
    assert c.sportmonks_fixture(d,meta('sportmonks'),b,match_identity(p))['teams']['home']['lineup']['status'] == 'confirmed'


def test_failed_fetch_archives_bytes_and_does_not_log_key(tmp_path, monkeypatch):
    opener = Mock(); body = b'synthetic security block'
    opener.open.side_effect = HTTPError('https://api-football.com/synthetic',567,'blocked',{},BytesIO(body))
    monkeypatch.setattr(c,'build_opener', lambda handler: opener)
    raw, m = c.fetch('https://api-football.com/synthetic','api_football',tmp_path/'capture',headers={'x-apisports-key':'secret'})
    assert raw == body and m['status'] == 'blocked'
    assert 'secret' not in (tmp_path/'capture'/'response.meta.json').read_text()
    assert c.NoRedirect().redirect_request(None,None,302,'',{},'https://other.test') is None
    with pytest.raises(ValueError, match='密钥'):
        c.fetch('https://api-football.com/synthetic?api%5Fkey=secret','api_football',tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


def test_import_rejects_changed_bytes_and_normalized_evidence_is_external(tmp_path):
    p = payload(season='2029/30'); m = meta(); b = packet(p)['binding']
    raw = json.dumps(espn_data(p)).encode(); m['sha256'] = hashlib.sha256(raw).hexdigest()
    q = import_raw(p,raw,m,b,match_identity(p),'espn')
    path = tmp_path/'evidence'; path.write_bytes(raw)
    verify_packets([q],{'0:http':str(path)})
    enriched, report = enrich(p,[q])
    assert report['status'] == 'partial' and 'team_observations' not in p
    assert enriched['snapshot_at'] == p['snapshot_at']
    with pytest.raises(ValueError,match='摘要'): import_raw(p,raw+b'changed',m,b,match_identity(p),'espn')
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='摘要'): verify_packets([q],{'0:http':str(path)})
    with pytest.raises(ValueError,match='其他项目'): enrich({**p,'project':'beidan'},[q])


def test_research_export_binds_rebuilt_observations_and_rejects_tampering():
    p = bound(); row = ledger_row(); row['payload']['input'] = p
    r = row['payload']['result']; r['input_sha256'] = input_digest(p)
    r['match'] = {k:p[k] for k in ('home','away','kickoff_at')}
    r['team_intelligence'] = {'status':'missing','schema':'jingcai-team-intelligence-v1','probability_effect_enabled':False}
    from engine.jingcai_team_intelligence import build_intelligence
    r['team_intelligence'] = build_intelligence(p,datetime.fromisoformat(p['snapshot_at']))
    r['team_observations'] = result(p)
    row['league'] = p['competition']
    exported = extract_rows([row],asof='2030-01-10T12:00:00+08:00')
    assert len(exported['feature_rows']) == 1
    assert exported['feature_rows'][0]['source_observations']['status'] == 'ok'
    r['team_observations']['selected_by_side']['home']['features']['season_red_total'] = 99
    assert extract_rows([row],asof='2030-01-10T12:00:00+08:00')['excluded'] == {'mismatched_stored_observations':1}
