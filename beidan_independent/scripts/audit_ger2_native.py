"""Audit the archived ESPN batch and emit provider-native research predictions.

The local file index is a Codex transcription of source receipts, not a new
provider payload. Chinese Beidan bindings remain explicit unapproved proposals.
"""
from datetime import datetime,timezone
import hashlib,json,math
from pathlib import Path
from beidan_bd1.snapshot import _datetime
from beidan_bd1.espn_schedule import parse_schedule,deduplicate
from beidan_bd1.espn_summary import audit_summary
from beidan_bd1.espn_fixture import audit_fixture
from beidan_bd1.provider_native import predict_espn_native

root=Path(__file__).resolve().parents[1]
folder=root/'data_sample/espn_ger2'
now=datetime.now(timezone.utc).isoformat()
index=json.loads((folder/'VERIFIED_FILE_INDEX.json').read_text())
rows=[];summaries={};checks=[]
for entry in index['files']:
    raw=(folder/entry['local_file']).read_bytes();sha=hashlib.sha256(raw).hexdigest()
    assert sha==entry['sha256'] and len(raw)==entry['bytes']
    assert entry['http_status']==200
    assert _datetime(entry['start_utc'],'start')<=_datetime(entry['end_utc'],'end')<=_datetime(now,'now')
    p=json.loads(raw)
    if entry['kind']=='schedule':
        records,report=parse_schedule(p,expected_team_id=entry['team_id'],verified_at=now,
                         league_slug='ger.2',season_year=2026,season_type=14359)
        rows.extend(records);checks.append({'file':entry['local_file'],**report})
    else:
        eid=entry['event_id']
        if eid in summaries:raise ValueError('duplicate summary')
        summaries[eid]=raw
unique=deduplicate(rows);audited=[];missing=[];rejected=[]
for r in unique:
    raw=summaries.get(r['provider_match_id'])
    if raw is None:missing.append(r['provider_match_id']);continue
    try:
        row,report=audit_summary(json.loads(raw),r,verified_at=now,raw_bytes=raw,league_slug='ger.2')
        audited.append({'record':row,'audit':report})
    except ValueError as error:rejected.append({'event_id':r['provider_match_id'],'reason':str(error)})
roster={r['seq']:r for r in json.loads((root/'outputs/20261010_179_shadow.json').read_text())['predictions']}
# Named proposals from archived source scoreboards, never automatic CN approval.
proposals=[(18,'401885100','6418','130','20261009'),(19,'401885094','3067','7884','20261009'),
           (89,'401885099','3812','123','20261010'),(90,'401885095','10382','2428','20261010'),
           (91,'401885093','7013','7017','20261010')]
predictions=[]
for seq,eid,h,a,date in proposals:
    raw=(root/f'data_sample/espn_scoreboard_bb0c436/scoreboard_ger.2_{date}.json').read_bytes()
    binding=audit_fixture(raw,event_id=eid,league_slug='ger.2',expected_home_id=h,expected_away_id=a,
                         roster=roster[seq],verified_at=now)
    event=next(e for e in json.loads(raw)['events'] if e['id']==eid)
    try:
        assert event['season']['year']==2026 and event['season']['type']==14359
        pred=predict_espn_native([r['record'] for r in audited],asof_at=now,kickoff_at=binding['kickoff_at'],
                 home_id=h,away_id=a,league_id=unique[0]['provider_league_id'],season_year=2026,
                 season_type=14359,handicap=roster[seq]['handicap'])
        pred.update(top5=sorted(pred['vectors']['score'].items(),key=lambda x:-x[1])[:5],
                    direction=max(pred['vectors']['wdl'],key=pred['vectors']['wdl'].get))
        assert all(abs(math.fsum(v.values())-1)<1e-8 for v in pred['vectors'].values())
        predictions.append({'seq':seq,'home':roster[seq]['home'],'away':roster[seq]['away'],
             'binding':binding,'official_handicap_status':'archived_line_not_fresh_verified',
             'status':'native_research_binding_unapproved','prediction':pred})
    except (ValueError,AssertionError) as error:
        predictions.append({'seq':seq,'status':'blocked','reason':str(error),'binding':binding})
report={'verified_at':now,'source_commit':index['source_commit'],'schedule_observations_n':len(rows),
    'unique_ft_n':len(unique),'summary_raw_n':len(summaries),'audited_n':len(audited),
    'explicit_ht_n':sum(r['audit']['explicit_halftime_available'] for r in audited),
    'missing_summary_ids':missing,'rejected':rejected,'canonical_identity_approved_n':0,
    'production_eligible':False,'brier':None,'walk_forward_eligible_historical_cutoffs':False,
    'source_scope':'observed schedule union; not independently proven full league season',
    'schedule_checks':checks,'results':audited,'research_predictions':predictions}
out=root/'outputs/ger2_native';out.mkdir(exist_ok=True)
path=out/'audit_and_predictions.json';path.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
print(json.dumps({k:v for k,v in report.items() if k not in ('results','research_predictions','schedule_checks')}))
print(json.dumps([{'seq':r['seq'],'status':r['status'],'wdl':r.get('prediction',{}).get('vectors',{}).get('wdl'),
                  'top5':r.get('prediction',{}).get('top5'),'reason':r.get('reason')} for r in predictions],ensure_ascii=False))
