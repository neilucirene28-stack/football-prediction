"""合成API契约，只证明数据纪律，不代表已接通付费接口或提高准确率。"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from collector import jingcai_intelligence as c
from collector import jingcai_api_football as a
from engine.jingcai_observations import SCHEMA, build_observations, match_identity, merge_same_source
from engine.jingcai_predictor import predict, PredictError
from scripts.import_jingcai_observations import enrich, import_api_pages, import_raw, verify_packets
from scripts.jingcai_format import format_team_intelligence
from test_jingcai_inputs_grid import payload, ASOF, CONFIG
from test_jingcai_source_observations import bound, packet


def binding():
    return {'event_id':'7','home_id':'1','away_id':'2','competition_id':'3','season_id':'2029'}


def match():
    return match_identity(payload(season='2029/30'))


def fixture():
    return {'get':'fixtures','parameters':{'id':'7'},'errors':[], 'results':1,
            'response':[{'fixture':{'id':7,'date':match()['kickoff_at'],'timestamp':int(datetime.fromisoformat(match()['kickoff_at']).timestamp()),'status':{'short':'NS'}},
                         'league':{'id':3,'season':2029},'teams':{'home':{'id':1},'away':{'id':2}}}]}


def page(n=1,total=2,pid=None,tid=1):
    ids=[pid] if pid is not None else list(range((n-1)*20+1,n*20+1)) if n<total else [(n-1)*20+1]
    return {'get':'players','parameters':{'team':str(tid),'league':'3','season':'2029','page':str(n)},'errors':[],
            'results':len(ids),'paging':{'current':n,'total':total},
            'response':[{'player':{'id':i,'name':f'合成球员{i}','nationality':'Brazil'},
                         'statistics':[{'team':{'id':tid},'league':{'id':3,'season':2029},'games':{'minutes':900},
                                        'goals':{'total':3,'assists':1},'cards':{'yellow':2,'red':1,'yellowred':1}}]}for i in ids]}


def meta(data,name):
    raw=json.dumps(data).encode()
    m={'source':'api_football','url':'https://v3.football.api-sports.io/'+name,'status':'ok','http_status':200,
       'sha256':hashlib.sha256(raw).hexdigest(),'collected_at':payload()['snapshot_at'],'available_at':payload()['snapshot_at']}
    return raw,m


def bundle():
    fd=fixture(); pages=[page(1),page(2)]
    return a.bundle(fd,meta(fd,'fixtures?id=7')[1],pages,[meta(d,f'players?page={i+1}')[1]for i,d in enumerate(pages)],binding(),match(),'players')


def observed(q):
    p=payload(season='2029/30');p['team_observations']={'schema':SCHEMA,'project':'jingcai','match':match(),'packets':[q]}
    return p,build_observations(p,datetime.fromisoformat(p['snapshot_at']))


def test_complete_pages_keep_independent_evidence_and_do_not_claim_roster_complete():
    q=bundle();p,r=observed(q);h=r['selected_by_side']['home']
    assert q['pagination']['complete'] and len(q['evidence'])==3
    assert q['fixture_context']['status']=='NS' and q['binding']['evidence_id']=='fixture'
    assert h['features']['observed_season_yellow_sum']==42 and h['features']['observed_season_red_sum']==21
    assert h['features']['season_yellow_total'] is None and not h['roster_complete']
    assert h['coverage']['season_stat_known_n']['season_minutes']==21
    assert q['teams']['home']['players'][-1]['season_stats_evidence_id']=='data-page-2'
    assert q['teams']['home']['players'][0]['registration']['category']=='unknown'
    base=predict(payload(season='2029/30'),CONFIG,asof=ASOF);new=predict(p,CONFIG,asof=ASOF)
    assert base['p_final_full']==new['p_final_full'] and base['score_matrix_full']==new['score_matrix_full']


@pytest.mark.parametrize('kind',['fixture_id','query','home','away','league','season','kickoff','timestamp','live','post','postponed','empty','double','business_error'])
def test_fixture_identity_state_and_time_fail_closed(kind):
    d=fixture();r=d['response'][0]
    if kind=='fixture_id':r['fixture']['id']=8
    elif kind=='query':d['parameters']['id']='8'
    elif kind in ('home','away'):r['teams'][kind]['id']=9
    elif kind=='league':r['league']['id']=9
    elif kind=='season':r['league']['season']=2030
    elif kind=='kickoff':r['fixture']['date']=payload()['snapshot_at']
    elif kind=='timestamp':r['fixture']['timestamp']+=1
    elif kind=='live':r['fixture']['status']['short']='1H'
    elif kind=='post':r['fixture']['status']['short']='FT'
    elif kind=='postponed':r['fixture']['status']['short']='PST'
    elif kind=='empty':d['response']=[]
    elif kind=='double':d['response'].append(deepcopy(r))
    elif kind=='business_error':d['errors']={'requests':'synthetic quota error'}
    with pytest.raises(ValueError):a.fixture_context(d,binding(),match())


@pytest.mark.parametrize('kind',['missing','duplicate','wrong_team','wrong_league','wrong_season','wrong_query_page',
                                 'total_changed','duplicate_player','zero_page','bool_page','count','float_count','http_failed','business_error'])
def test_pagination_scope_and_completeness_rejected(kind):
    pages=[page(1),page(2)]
    if kind=='missing':pages.pop()
    elif kind=='duplicate':pages[1]=deepcopy(pages[0])
    elif kind=='wrong_team':pages[1]['parameters']['team']='2'
    elif kind=='wrong_league':pages[1]['response'][0]['statistics'][0]['league']['id']=9
    elif kind=='wrong_season':pages[1]['parameters']['season']='2030'
    elif kind=='wrong_query_page':pages[1]['parameters']['page']='1'
    elif kind=='total_changed':pages[1]['paging']['total']=3
    elif kind=='duplicate_player':pages[1]['response'][0]['player']['id']=1
    elif kind=='zero_page':pages[1]['paging']['current']=0
    elif kind=='bool_page':pages[1]['paging']['current']=True
    elif kind=='count':pages[1]['results']=20
    elif kind=='float_count':pages[1]['results']=1.0
    elif kind=='business_error':pages[1]['errors']={'requests':'failed'}
    metas=[meta(d,f'players?page={i+1}')[1]for i,d in enumerate(pages)]
    if kind=='http_failed':metas[1]['http_status']=429;metas[1]['status']='blocked'
    with pytest.raises(ValueError):a.bundle(fixture(),meta(fixture(),'fixtures?id=7')[1],pages,metas,binding(),match(),'players')


def test_out_of_order_pages_are_sorted_by_native_page_and_no_unrequested_team_stats():
    first=page(1);first['response'][0]['statistics'].append(deepcopy(first['response'][0]['statistics'][0]))
    first['response'][0]['statistics'][1]['team']['id']=2
    second=page(2)
    q=a.bundle(fixture(),meta(fixture(),'fixtures?id=7')[1],[second,first],[meta(second,'players?page=2')[1],meta(first,'players?page=1')[1]],binding(),match(),'players')
    assert [p['player_id']for p in q['teams']['home']['players']]==[str(i)for i in range(1,22)] and q['teams']['away'] is None


@pytest.mark.parametrize('kind',['truncated_nonfinal','oversized_final'])
def test_pagination_body_size_matches_documented_page_contract(kind):
    pages=[page(1),page(2)]
    if kind=='truncated_nonfinal':pages[0]['response'].pop();pages[0]['results']=19
    else:pages[1]['response']=[deepcopy(pages[1]['response'][0])for _ in range(21)];pages[1]['results']=21
    with pytest.raises(ValueError,match='20条'):
        a.bundle(fixture(),meta(fixture(),'fixtures?id=7')[1],pages,[meta(d,'players')[1]for d in pages],binding(),match(),'players')


def test_missing_fixture_context_is_rejected_at_model_boundary():
    q=bundle();q.pop('fixture_context');p,_=observed(bundle())
    p['team_observations']['packets']=[q]
    with pytest.raises(PredictError,match='赛前赛程'):predict(p,CONFIG,asof=ASOF)


def mock_fetch(monkeypatch, *, fail_page=None, total=2, quota=None, bad_fixture=False):
    calls=[]
    def fetch(url,source,directory,**kwargs):
        calls.append(url);params=parse_qs(urlsplit(url).query)
        if 'id' in params:
            d=fixture()
            if bad_fixture:d['response'][0]['teams']['home']['id']=99
        else:d=page(int(params['page'][0]),total)
        raw,m=meta(d,urlsplit(url).path.lstrip('/')+'?'+urlsplit(url).query)
        if fail_page is not None and params.get('page')==[str(fail_page)]:m.update(http_status=429,status='blocked')
        if quota is not None:m['quota']={'daily_remaining':quota}
        path=Path(directory);path.mkdir(parents=True,exist_ok=False)
        (path/'response.body').write_bytes(raw);(path/'response.meta.json').write_text(json.dumps(m))
        return raw,m
    monkeypatch.setattr(c,'fetch',fetch)
    return calls


def test_live_collect_validates_fixture_before_pagination_and_preserves_raw_files(tmp_path,monkeypatch):
    calls=mock_fetch(monkeypatch)
    q=c.collect('api_football',binding(),match(),tmp_path/'capture',kind='players',key='synthetic-key')
    assert len(calls)==3 and '/fixtures?id=7' in calls[0]
    assert q['status']=='ok' and q['pagination']['complete']
    assert (tmp_path/'capture/data-page-2/response.body').exists()
    assert 'synthetic-key' not in json.dumps(q)


@pytest.mark.parametrize('kind',['second_page_blocked','bad_fixture','budget','quota'])
def test_live_failure_stops_and_never_publishes_partial_player_statistics(kind,tmp_path,monkeypatch):
    calls=mock_fetch(monkeypatch,fail_page=2 if kind=='second_page_blocked'else None,total=20 if kind=='budget'else 2,
                     quota=0 if kind=='quota'else None,bad_fixture=kind=='bad_fixture')
    q=c.collect('api_football',binding(),match(),tmp_path/'capture',kind='players',key='synthetic-key')
    assert q['status']!='ok' and q['teams']=={'home':None,'away':None}
    assert not q['pagination']['complete']
    assert len(calls)=={'second_page_blocked':3,'bad_fixture':1,'budget':2,'quota':1}[kind]
    assert not (tmp_path/'capture/data-page-3').exists()


def test_no_key_no_past_match_and_invalid_binding_make_no_requests(tmp_path,monkeypatch):
    calls=mock_fetch(monkeypatch)
    with pytest.raises(ValueError,match='KEY'):c.collect('api_football',binding(),match(),tmp_path/'capture',kind='players')
    past={**match(),'kickoff_at':'2020-01-01T00:00:00+00:00'}
    with pytest.raises(ValueError,match='已开球'):c.collect('api_football',binding(),past,tmp_path/'capture',kind='players',key='synthetic-key')
    with pytest.raises(ValueError,match='原生ID'):c.collect('api_football',{**binding(),'season_id':'2029/30'},match(),tmp_path/'capture',kind='players',key='synthetic-key')
    assert calls==[] and not (tmp_path/'capture').exists()


def test_raw_import_verifies_fixture_bytes_and_all_pages(tmp_path):
    fr,fm=meta(fixture(),'fixtures?id=7');pairs=[meta(page(1),'players?page=1'),meta(page(2),'players?page=2')]
    p=payload(season='2029/30')
    q=import_api_pages([x[0]for x in pairs],[x[1]for x in pairs],fr,fm,binding(),match())
    _,r=enrich(p,[q]);assert r['status']=='partial'
    with pytest.raises(ValueError,match='摘要'):import_api_pages([x[0]for x in pairs],[x[1]for x in pairs],fr+b'changed',fm,binding(),match())
    with pytest.raises(ValueError,match='赛程原件'):import_raw(p,pairs[0][0],pairs[0][1],binding(),match(),'api-players')
    files={}
    for e,raw in zip(q['evidence'],[fr]+[x[0]for x in pairs]):
        path=tmp_path/e['id'];path.write_bytes(raw);files['0:'+e['id']]=str(path)
    verify_packets([q],files)
    bad=deepcopy(q);bad['fixture_context']['home_id']='9'
    with pytest.raises(ValueError,match='不一致'):verify_packets([bad],files)


def test_cli_all_pages_enriches_new_input_and_refuses_existing_output(tmp_path):
    p=payload(season='2029/30');(tmp_path/'input.json').write_text(json.dumps(p))
    (tmp_path/'binding.json').write_text(json.dumps({'binding':binding(),'match':match()}))
    for name,data in [('fixture',fixture()),('page1',page(1)),('page2',page(2))]:
        raw,m=meta(data,'fixtures?id=7'if name=='fixture'else'players')
        (tmp_path/(name+'.body')).write_bytes(raw);(tmp_path/(name+'.meta.json')).write_text(json.dumps(m))
    (tmp_path/'pages.json').write_text(json.dumps([{'raw':'page1.body','meta':'page1.meta.json'},{'raw':'page2.body','meta':'page2.meta.json'}]))
    command=[sys.executable,'scripts/import_jingcai_observations.py','--payload',str(tmp_path/'input.json'),
             '--api-pages',str(tmp_path/'pages.json'),'--fixture-raw',str(tmp_path/'fixture.body'),'--fixture-meta',str(tmp_path/'fixture.meta.json'),
             '--binding',str(tmp_path/'binding.json'),'--output',str(tmp_path/'new-input.json')]
    completed=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['created_input_only_not_sealed_forecast']
    assert json.loads((tmp_path/'new-input.json').read_text())['team_observations']['packets'][0]['pagination']['pages']==2
    assert subprocess.run(command,cwd=Path(__file__).resolve().parents[1],capture_output=True).returncode!=0


def test_freshness_and_coverage_do_not_replace_unknowns_with_zero():
    p=bound();q=p['team_observations']['packets'][0]
    q['evidence'][0]['collected_at']='2030-01-01T08:00:00+08:00'
    q['evidence'][0]['available_at']='2030-01-01T09:00:00+08:00'
    x=q['teams']['home']['players'][0];x['season_red']=None;x['registration']=None
    r=build_observations(p,datetime.fromisoformat(p['snapshot_at']))
    source=r['sources'][0];h=r['selected_by_side']['home']
    assert source['evidence_ages']['http']['collected_age_seconds']==7200
    assert source['evidence_ages']['http']['available_age_seconds']==3600
    assert source['evidence_ages']['http']['published_age_seconds'] is None
    assert h['coverage']['season_stat_known_n']['season_red']==11
    assert h['features']['season_red_total'] is None and h['coverage']['registration_known_n']==11
    assert any('120.0' in line for line in format_team_intelligence({'team_observations':r}))


def test_same_source_merge_references_fixture_after_evidence_rename():
    q=bundle();other=deepcopy(q)
    other['teams']['home']=None;other['teams']['away']={'team_id':'2','player_id_namespace':'api_football','roster_complete':False,'lineup':None,
                                                    'players':[c._player('99','合成客队球员')]}
    merged=merge_same_source([q,other])
    _,r=observed(merged[0]);assert r['status']=='ok' and not r['conflicts']
    assert merged[0]['fixture_context']['evidence_id']=='0:fixture'


def test_enrichment_retains_prior_sources_unless_explicitly_replaced():
    p=bound();before=deepcopy(p);new=packet(p,'sporttery')
    enriched,r=enrich(p,[new])
    assert p==before and len(enriched['team_observations']['packets'])==2
    assert [x['source']for x in r['sources']]==['espn','sporttery']
    replaced,_=enrich(p,[new],replace_observations=True)
    assert len(replaced['team_observations']['packets'])==1 and p==before


def test_appending_duplicate_capture_is_rejected_instead_of_counted_twice():
    p=bound()
    with pytest.raises(ValueError,match='重复来源包'):
        enrich(p,deepcopy(p['team_observations']['packets']))
