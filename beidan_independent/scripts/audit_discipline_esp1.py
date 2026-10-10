"""Extract card observations from retained audited results; no network/refit."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from beidan_bd1.artifact_io import read_artifact
from beidan_bd1.discipline import extract_observation, team_features

ROOT=Path(__file__).resolve().parents[1]


def audit():
    folder=ROOT/'data_sample/espn_esp.1_mined_20261010_v25'
    raw_audit=read_artifact(ROOT/'outputs/v25_native/esp.1/audit_and_predictions.json')
    if hashlib.sha256(raw_audit).hexdigest()!='58925fbb8001c223f9705f9c8bb42e5446ae4bbe9aa6354ad0f21012f8183a1d':
        raise ValueError('original historical audit digest differs')
    verified=json.loads(raw_audit);raw_index=(folder/'VERIFIED_FILE_INDEX.json').read_bytes()
    if hashlib.sha256(raw_index).hexdigest()!=verified['file_index_sha256']:
        raise ValueError('original capture index digest differs')
    index=json.loads(raw_index);receipts={r['event_id']:r for r in index['files'] if r['kind']=='summary'}
    now=datetime.now(timezone.utc).isoformat();rows=[]
    for original in verified['results']:
        record=original['record'];receipt=receipts[record['provider_match_id']]
        if receipt['http_status']!=200 or record['summary_sha256']!=receipt['sha256']:
            raise ValueError('original summary receipt differs')
        raw=read_artifact(folder/receipt['local_file'])
        if len(raw)!=receipt['bytes']:
            raise ValueError('original summary size differs')
        rows.append(extract_observation(raw,schedule=record,league_slug='esp.1',
            source_available_at=receipt['end_utc'],verified_at=now,expected_sha256=receipt['sha256']))
    teams=sorted({r['sides'][s]['team_id'] for r in rows for s in ('home','away')})
    features=[team_features(rows,team_id=t,league_id='740',season_year=2026,season_type=14357,asof_at=now) for t in teams]
    return {'audited_at':now,'source_audit_sha256':hashlib.sha256(raw_audit).hexdigest(),
            'source_index_sha256':hashlib.sha256(raw_index).hexdigest(),
            'observed_matches_n':len(rows),'observed_teams_n':len(teams),'observations':rows,
            'team_features':features,'complete_season_coverage':False,
            'new_qualified_forecast_n':0,'goal_model_adjusted':False,'production_eligible':False}


if __name__=='__main__':
    result=audit();path=ROOT/'outputs/v30_esp1_discipline_audit.json'
    with path.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'path':str(path),'matches':result['observed_matches_n'],'teams':result['observed_teams_n']}))
