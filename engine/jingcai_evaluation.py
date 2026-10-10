"""Select earliest stored prospective predictions per match and model version.

Database timestamps are checked for consistency, not called authenticated source
receipts. Historical rows lacking generation metadata remain observation-only.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from .backtest import brier_score, log_loss, calibration_by_class


def dt(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None: raise ValueError('timezone required')
    return result


def select_earliest(rows, now=None):
    now = now or datetime.now(timezone.utc)
    excluded = Counter(); groups = defaultdict(list)
    for row in rows:
        try:
            body = row.get('payload') or {}
            result = body.get('result') or {}
            timing = result.get('prediction_timing') or {}
            if result.get('model') != 'jingcai' or timing.get('mode') != 'prospective':
                excluded['not_jingcai_prospective'] += 1; continue
            generated, predicted, kickoff = map(dt,(timing['generated_at'],row['predicted_at'],row['kickoff_at']))
            snapshot = dt(timing['snapshot_at']); started = dt(timing['started_at'])
            if not snapshot <= started <= generated == predicted < kickoff or generated > now:
                excluded['inconsistent_prediction_clock'] += 1; continue
            if (dt((result.get('match') or {})['kickoff_at']) != kickoff
                    or result.get('model_version') != row['model_version']):
                excluded['mismatched_result_identity'] += 1; continue
            p = result['p_final_full']
            if len(p)!=3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in p) or abs(sum(p)-1)>1e-8:
                excluded['invalid_full_precision_probabilities'] += 1; continue
            stored = [row[k] for k in ('p_home','p_draw','p_away')]
            if any(abs(a-b)>1e-10 for a,b in zip(p,stored)):
                excluded['stored_probability_mismatch'] += 1; continue
            if row.get('match_id') is None or not row.get('prediction_id') or not row.get('model_version'):
                excluded['missing_prediction_identity'] += 1; continue
            groups[(row['match_id'],row['model_version'])].append(row)
        except (ValueError,TypeError,KeyError,AttributeError):
            excluded['missing_or_invalid_metadata'] += 1
    selected=[]; duplicates=0; invalid_settlements=0
    for candidates in groups.values():
        candidates.sort(key=lambda r:(dt(r['predicted_at']),str(r['prediction_id'])))
        if len({dt(r['kickoff_at']) for r in candidates}) != 1:
            excluded['conflicting_kickoff_identity'] += len(candidates);continue
        first_time=dt(candidates[0]['predicted_at'])
        ties=[r for r in candidates if dt(r['predicted_at'])==first_time]
        if len({tuple(r['payload']['result']['p_final_full']) for r in ties}) > 1:
            excluded['ambiguous_earliest_probabilities'] += len(candidates);continue
        earliest=dict(candidates[0]);duplicates+=len(candidates)-1
        hg,ag=earliest.get('home_goals'),earliest.get('away_goals')
        invalid=(hg is None)!=(ag is None) or any(type(v) is not int or v < 0 for v in (hg,ag) if v is not None)
        if hg is not None:
            try:
                invalid = invalid or not dt(earliest['kickoff_at']) <= dt(earliest['settled_at']) <= now
            except (KeyError,TypeError,ValueError,AttributeError):invalid=True
        if invalid:
            invalid_settlements+=1;earliest['home_goals']=None;earliest['away_goals']=None
        selected.append(earliest)
    return selected, {'input_rows':len(rows),'excluded_rows':sum(excluded.values()),
                      'exclusion_reasons':dict(excluded),'later_duplicate_predictions':duplicates,
                      'selected_match_version_pairs':len(selected),
                      'unique_matches':len({r['match_id'] for r in selected}),
                      'invalid_selected_settlements':invalid_settlements,
                      'pending_selected_predictions':sum(r.get('home_goals') is None for r in selected),
                      'independent_source_provenance_verified':False}


def summarize_version(rows):
    settled=[r for r in rows if r.get('home_goals') is not None]
    data=[(tuple(r['payload']['result']['p_final_full']),
           0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2)
          for r in settled]
    n=len(data)
    return {'n':n,'pending':len(rows)-n,
            'brier':sum(brier_score(p,y) for p,y in data)/n if n else None,
            'logloss':sum(log_loss(p,y) for p,y in data)/n if n else None,
            'direction_accuracy':sum(max(range(3),key=lambda i:p[i])==y for p,y in data)/n if n else None,
            'calibration_by_class':calibration_by_class(data)}


def evaluation_report(rows):
    selected,audit=select_earliest(rows)
    groups=defaultdict(list)
    for r in selected:groups[r['model_version']].append(r)
    versions={v:summarize_version(rs) for v,rs in sorted(groups.items())}
    return {'status':'ok' if selected else 'no_data','audit':audit,'by_version':versions,
            'n':sum(r['n'] for r in versions.values()),
            'note':'同场同版本仅保留最早合格记录；跨版本分别评估。时间一致性不等于独立来源封存。'}
