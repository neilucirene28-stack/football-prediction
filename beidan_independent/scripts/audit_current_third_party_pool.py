"""Audit current source observations; no official status or CN ID approval."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from beidan_bd1.archive_io import read_response
from beidan_bd1.snapshot import _datetime
from beidan_bd1.third_party_pool import parse_pool_html


def main():
    root=Path(__file__).resolve().parents[1]
    folder=root/'data_sample/current_pool_third_party_probe_20261009'
    raw=read_response(folder,'response.html'); receipt=json.loads((folder/'RECEIPT.json').read_bytes())
    now=datetime.now(timezone.utc).isoformat()
    if (receipt['http_status']!=200 or receipt['bytes']!=len(raw)
            or receipt['sha256']!=hashlib.sha256(raw).hexdigest()
            or not _datetime(receipt['start_utc'],'start')<=_datetime(receipt['end_utc'],'end')<=_datetime(now,'now')):
        raise ValueError('raw page differs from actual capture receipt')
    rows, audit=parse_pool_html(raw); by_seq={r['seq']:r for r in rows}
    pool=json.loads((root/'outputs/20261010_179_shadow.json').read_bytes())['predictions']
    if len(pool)!=179 or len({r['seq'] for r in pool})!=179:raise ValueError('archived full pool differs')
    records=[]
    for offered in pool:
        row=by_seq.get(offered['seq'])
        if row is None:
            records.append({'seq':offered['seq'],'source_row_present':False});continue
        if row['period']!=offered['period']:raise ValueError('current period differs from archived roster')
        def names(side):
            return {row['teams'][side]['title'], row['teams'][side]['display_name'],
                    row['metadata']['homeTeam' if side=='home' else 'guestTeam']}
        ko=_datetime(offered['kickoff_at'],'archive kickoff').astimezone(timezone(timedelta(hours=8)))
        cutoff=datetime.strptime(row['sale_cutoff_local'],'%Y-%m-%d %H:%M')
        complete=False
        try:
            sp=[Decimal(s) for s in row['reference_sp_observed']]
            complete=len(sp)==3 and all(s.is_finite() and s>=1 for s in sp)
        except InvalidOperation:pass
        records.append({'seq':offered['seq'],'home':offered['home'],'away':offered['away'],
          'league':offered['league'],'source_row_present':True,'source_observation':row,
          'home_name_literal_matches':offered['home'] in names('home'),
          'away_name_literal_matches':offered['away'] in names('away'),
          'kickoff_clock_matches_archive':ko.strftime('%H:%M')==row['kickoff_clock_only'],
          'sale_cutoff_consistency_assuming_Beijing':0<(ko.replace(tzinfo=None)-cutoff).total_seconds()<=3600,
          'source_cutoff_timezone_independently_verified':False,
          'line_matches_archive':row['wdl_handicap_observed']==offered['handicap'],
          'reference_sp_complete':complete,'source_id_verified':False,
          'fixture_binding_approved':False,'official_line_imported':False,'sp_imported':False,
          'production_eligible':False})
    summary={**audit,'verified_at':now,'raw_sha256':receipt['sha256'],'source_url':receipt['url'],
      'request_end_at':receipt['end_utc'],'archived_pool_n':179,
      'source_row_present_n':sum(r['source_row_present'] for r in records),
      'both_names_literal_match_n':sum(r.get('home_name_literal_matches',False) and r.get('away_name_literal_matches',False) for r in records),
      'name_difference_n':sum(r.get('source_row_present',False) and not(r.get('home_name_literal_matches',False) and r.get('away_name_literal_matches',False)) for r in records),
      'kickoff_clock_matches_n':sum(r.get('kickoff_clock_matches_archive',False) for r in records),
      'line_matches_archive_n':sum(r.get('line_matches_archive',False) for r in records),
      'reference_sp_complete_n':sum(r.get('reference_sp_complete',False) for r in records),
      'canonical_identity_approved_n':0,'official_source':False,'model_imported_n':0,
      'scope':'third_party_source_observations_do_not_promote_research_or_official_coverage',
      'records':records}
    path=root/'outputs/current_third_party_pool_20261009_audit.json'
    with path.open('x') as fh:json.dump(summary,fh,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in summary.items() if k!='records'},ensure_ascii=False))


if __name__=='__main__':main()
