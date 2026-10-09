"""Audit the archived ESPN batch and emit provider-native research predictions.

The local file index is a Codex transcription of source receipts, not a new
provider payload. Chinese Beidan bindings remain explicit unapproved proposals.
"""
import argparse
from datetime import datetime,timezone
import hashlib,json,math
from pathlib import Path
from beidan_bd1.snapshot import _datetime
from beidan_bd1.espn_schedule import parse_schedule,deduplicate
from beidan_bd1.espn_summary import audit_summary
from beidan_bd1.espn_fixture import audit_fixture
from beidan_bd1.provider_native import predict_espn_native
from beidan_bd1.archive_io import read_response

root=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('config');args=ap.parse_args()
config=json.loads(Path(args.config).read_text())
folder=root/config['folder']
slug=config['league_slug'];year=config['season_year'];stage=config['season_type']
now=datetime.now(timezone.utc).isoformat()
index=json.loads((folder/'VERIFIED_FILE_INDEX.json').read_text())
rows=[];summaries={};checks=[]
for entry in index['files']:
    raw=read_response(folder,entry['local_file']);sha=hashlib.sha256(raw).hexdigest()
    if sha!=entry['sha256'] or len(raw)!=entry['bytes'] or entry['http_status']!=200:
        raise ValueError('raw response differs from successful HTTP receipt')
    if not _datetime(entry['start_utc'],'start')<=_datetime(entry['end_utc'],'end')<=_datetime(now,'now'):
        raise ValueError('HTTP receipt clocks are invalid or later than verification')
    p=json.loads(raw)
    if entry['kind']=='schedule':
        records,report=parse_schedule(p,expected_team_id=entry['team_id'],verified_at=now,
                         league_slug=slug,season_year=year,season_type=stage)
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
        row,report=audit_summary(json.loads(raw),r,verified_at=now,raw_bytes=raw,league_slug=slug)
        audited.append({'record':row,'audit':report})
    except ValueError as error:rejected.append({'event_id':r['provider_match_id'],'reason':str(error)})
roster={r['seq']:r for r in json.loads((root/'outputs/20261010_179_shadow.json').read_text())['predictions']}
# Named proposals from archived source scoreboards, never automatic CN approval.
proposals=config['proposals']
predictions=[]
for seq,eid,h,a,date in proposals:
    raw=(root/f"{config['scoreboard_folder']}/scoreboard_{slug}_{date}.json").read_bytes()
    binding=audit_fixture(raw,event_id=eid,league_slug=slug,expected_home_id=h,expected_away_id=a,
                         roster=roster[seq],verified_at=now)
    event=next(e for e in json.loads(raw)['events'] if e['id']==eid)
    try:
        if event['season']['year']!=year or event['season']['type']!=stage:
            raise ValueError('fixture season differs from audited history')
        pred=predict_espn_native([r['record'] for r in audited],asof_at=now,kickoff_at=binding['kickoff_at'],
                 home_id=h,away_id=a,league_id=unique[0]['provider_league_id'],season_year=year,
                 season_type=stage,handicap=roster[seq]['handicap'])
        pred.update(top5=sorted(pred['vectors']['score'].items(),key=lambda x:-x[1])[:5],
                    direction=max(pred['vectors']['wdl'],key=pred['vectors']['wdl'].get))
        if not all(abs(math.fsum(v.values())-1)<1e-8 for v in pred['vectors'].values()):
            raise ValueError('prediction probability vectors are not normalized')
        predictions.append({'seq':seq,'home':roster[seq]['home'],'away':roster[seq]['away'],
             'binding':binding,'official_handicap_status':'archived_line_not_fresh_verified',
             'status':'native_research_binding_unapproved','prediction':pred})
    except (ValueError,AssertionError) as error:
        predictions.append({'seq':seq,'status':'blocked','reason':str(error),'binding':binding})
report={'verified_at':now,'source_commit':index['source_commit'],'schedule_observations_n':len(rows),
    'audit_code_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__).resolve(),root/'beidan_bd1/espn_schedule.py',
                  root/'beidan_bd1/espn_summary.py',root/'beidan_bd1/espn_fixture.py',
                  root/'beidan_bd1/archive_io.py']},
    'unique_ft_n':len(unique),'summary_raw_n':len(summaries),'audited_n':len(audited),
    'explicit_ht_n':sum(r['audit']['explicit_halftime_available'] for r in audited),
    'missing_summary_ids':missing,'rejected':rejected,'canonical_identity_approved_n':0,
    'production_eligible':False,'brier':None,'walk_forward_eligible_historical_cutoffs':False,
    'source_scope':'observed schedule union; not independently proven full league season',
    'schedule_checks':checks,'results':audited,'research_predictions':predictions}
out=root/config['output_folder'];out.mkdir(exist_ok=True)
path=out/'audit_and_predictions.json';path.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
print(json.dumps({k:v for k,v in report.items() if k not in ('results','research_predictions','schedule_checks')}))
print(json.dumps([{'seq':r['seq'],'status':r['status'],'wdl':r.get('prediction',{}).get('vectors',{}).get('wdl'),
                  'top5':r.get('prediction',{}).get('top5'),'reason':r.get('reason')} for r in predictions],ensure_ascii=False))
