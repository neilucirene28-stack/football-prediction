"""Offline historical fixture audit; never calls an API, database, or existing prediction module.

Produces separate pre-match evidence and sealed outcome.  A report is NOT a
historical prediction or a model accuracy measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

class AuditError(ValueError):
    pass


def _dt(value, *, zone_required=False):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00').replace('/', '-'))
        if zone_required and dt.tzinfo is None:
            raise ValueError('timezone required')
        return dt
    except (TypeError, ValueError, AttributeError) as exc:
        raise AuditError('invalid timestamp') from exc


def _load(file):
    p = Path(file)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > 5_000_000:
        raise AuditError('input is not a regular JSON file <= 5 MB')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AuditError('invalid JSON') from exc
    if not isinstance(data, dict):
        raise AuditError('JSON root must be an object')
    return data, hashlib.sha256(p.read_bytes()).hexdigest()


def build(snapshot):
    match = snapshot['比赛']
    source = snapshot['来源']
    odds = snapshot['盘口快照']
    history = snapshot['盘口历史']
    kick = _dt(match['开赛时间_北京时间'], zone_required=True)
    observed = _dt(odds['采集时间_北京时间']).replace(tzinfo=kick.tzinfo)
    source_observed = _dt(source['文件最近更新_北京时间']).replace(tzinfo=kick.tzinfo)
    if observed >= kick or source_observed >= kick:
        raise AuditError('snapshot recorded at or after kickoff')
    main = odds['主盘_保留原始字段']
    extra = odds['附加盘口_原公司归属未验证']
    if len(main)+len(extra) != odds['原始公司盘口记录数']:
        raise AuditError('handicap record count mismatch')
    if len(main) != odds['明确公司标签主盘记录数'] or len(extra) != odds['附加盘口孤立记录数']:
        raise AuditError('main/additional record counts mismatch')
    rows=history['保留原始字段记录']
    if len(rows)!=history['原始记录数']:
        raise AuditError('history record count mismatch')
    eligible=[]; quarantined=[]
    for i,row in enumerate(rows):
        try:
            t=_dt(row['时间']).replace(tzinfo=kick.tzinfo)
            if t > observed or t >= kick:
                quarantined.append({'index':i, 'reason':'after snapshot cutoff'}); continue
            if not all(row.get(k) for k in ('公司','盘口','主水','客水')):
                quarantined.append({'index':i, 'reason':'missing required values'}); continue
            eligible.append(row)
        except (AuditError, KeyError):
            quarantined.append({'index':i, 'reason':'invalid timestamp or fields'})
    # Only the allowlisted fields below enter pre-match evidence. Never forward
    # the original complete JSON or embedded outcome/summary to an LLM.
    pre={
      'fixture_id':match['比赛ID'], 'home_team':match['主队'], 'away_team':match['客队'],
      'kickoff_at':kick.isoformat(), 'snapshot_cutoff_at':observed.isoformat(),
      'source_last_updated_at':source_observed.isoformat(),
      'handicap_main':main, 'handicap_additional_unattributed':extra,
      'handicap_history_through_cutoff':eligible,
      'independent_1x2_company_odds':'missing', 'independent_total_goals_market':'missing',
      'verified_starting_lineups':'missing',
      'limitations':['source authenticity not independently verified',
                     'per-row timestamps unavailable for snapshot company odds',
                     'masked bookmaker labels are not unique identities',
                     'this is a historical pre-match snapshot, not a contemporaneous model forecast']}
    result=match.get('全场比分')
    if not isinstance(result,dict) or any(type(result.get(k)) is not int or result[k]<0 for k in ('主队','客队')):
        raise AuditError('final score missing or invalid')
    a,b=result['主队'],result['客队']
    outcome={'full_time':{'home':a,'away':b},'result':'home' if a>b else 'away' if a<b else 'draw',
             'total_goals':a+b,
             'settlement_note':'With a 1:1 result, home +0.5 covers; home +0.25 is a half-win under standard Asian handicap rules.' if a==b else 'Handicap settlement requires applying each exact line.'}
    summary={'fixture_id':pre['fixture_id'], 'main_lines':dict(Counter(str(r.get('即时盘口','')) for r in main)),
             'main_records':len(main),'unattributed_additional_records':len(extra),
             'history_records_before_snapshot':len(eligible), 'history_quarantined':quarantined,
             'snapshot_cutoff_at':pre['snapshot_cutoff_at'],
             'status':'historical_audit_only_not_model_backtest',
             'prediction_record':'not supplied; accuracy cannot be calculated'}
    return pre,outcome,summary


def write_report(input_file, output_dir):
    snapshot, sha=_load(input_file)
    pre,outcome,summary=build(snapshot)
    out=Path(output_dir)
    if out.exists() and (not out.is_dir() or out.is_symlink()):
        raise AuditError('output directory invalid')
    out.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name,value in [('prematch_only.json',pre),('outcome_only.json',outcome),('audit_report.json',dict(summary, input_sha256=sha))]:
        target=out/name
        if target.exists() or target.is_symlink():
            raise AuditError('output already exists: '+name)
        with target.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
            f.write('\n')
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline historical snapshot audit (no forecast or API request)')
    parser.add_argument('--input',required=True)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args(argv)
    try:
        result=write_report(args.input,args.output_dir)
    except (AuditError, KeyError, OSError) as exc:
        parser.exit(2,'AUDIT_FAILED: '+str(exc)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
