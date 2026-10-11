"""只读导出已绑定的竞彩阵容研究特征；保留缺失，不拟合或写入概率。"""
from collections import Counter
from .jingcai_review import prepared_record, decoded, timestamp
from .jingcai_ledger import validate_live_record
from .jingcai_team_intelligence import build_intelligence
from .jingcai_observations import build_observations


def extract_rows(rows, *, asof):
    asof=timestamp(asof,'asof'); output=[];excluded=Counter();seen=set()
    for row in rows:
        try:
            scored,reason=prepared_record(row,asof)
            if reason: excluded[reason]+=1;continue
            saved=decoded(row['payload']);payload,result=saved['input'],saved['result']
            times=validate_live_record(payload,result,now=scored['predicted_at'],require_hash=True)
            if not row.get('source'): excluded['missing_settlement_source']+=1;continue
            rebuilt=build_intelligence(payload,times['snapshot'])
            observations=build_observations(payload,times['snapshot'])
            if payload.get('team_observations') is not None and observations!=result.get('team_observations'):
                excluded['mismatched_stored_observations']+=1;continue
            if rebuilt['status']=='missing' and observations['status'] not in ('ok','partial'):
                excluded['missing_team_intelligence']+=1;continue
            if rebuilt!=result.get('team_intelligence'):
                excluded['mismatched_stored_intelligence']+=1;continue
            key=(row['model_version'],row['match_id'],str(row.get('prediction_id','')))
            if key in seen: excluded['duplicate_export_row']+=1;continue
            seen.add(key)
            exported={'model_version':row['model_version'],'match_id':row['match_id'],
                'prediction_id':str(row.get('prediction_id','')),'kickoff_at':scored['kickoff_at'].isoformat(),
                'snapshot_at':times['snapshot'].isoformat(),'predicted_at':scored['predicted_at'].isoformat(),
                'settled_at':scored['settled_at'].isoformat(),'outcome':scored['outcome'],
                'baseline_probabilities_full':scored['probs_full'],
                'intelligence_input_sha256':rebuilt.get('input_sha256'),'source_sha256':rebuilt.get('source_sha256'),
                'season':(rebuilt.get('match') or observations.get('match'))['season'],'teams':rebuilt.get('teams'),
                'provenance_status':rebuilt.get('provenance_status'),
                'production_effect_enabled':False}
            if payload.get('team_observations') is not None:
                exported['source_observations']=observations
            output.append(exported)
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            excluded['invalid_bound_team_record']+=1
    return {'schema':'jingcai-team-feature-export-v1','scope':'jingcai_only','input_rows':len(rows),
            'feature_rows':output,'excluded':dict(excluded),'production_effect_enabled':False,
            'qualification':'research export, not independent training matches; separate versions, deduplicate '
                            'physical matches, resolve conflicts and enforce temporal splits before learning; '
                            'null features remain unknown; source meaning and original collection timestamps not authenticated'}
