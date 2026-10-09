"""Verify source files and record cross-provider literal agreement only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from beidan_bd1.frozen_settlement import load_bundle
from beidan_bd1.provider_concordance import compare_native_fixture


def main():
    root=Path(__file__).resolve().parents[1]
    manifest='5161386da56380f4faf8713c007f67e9b911a34c3b6e5314ba9b2b65086a57d0'
    frozen,_,_=load_bundle(root/'outputs/l1_prospective/20261009T150800070498Z',
                          manifest_sha256=manifest,evaluated_at=datetime.now(timezone.utc).isoformat())
    afraw=(root/'data_sample/af_true/af_fixtures_raw_20261010.json').read_bytes()
    afpath=root/'outputs/af_response_audit/audit_20261009T152401081861Z.json'
    af=json.loads(afpath.read_bytes())
    if af['raw_sha256']!=hashlib.sha256(afraw).hexdigest():raise ValueError('AF source differs from audit')
    records=[]
    for path,sha in frozen['prospective_freeze']['history_audits_sha256'].items():
        raw=Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('native audit differs from original freeze')
        batch=json.loads(raw)
        for item in batch['research_predictions']:
            compared=compare_native_fixture(item['binding'],af['records'])
            compared.update(seq=item['seq'],native_prediction_status=item['status'])
            records.append(compared)
    unique=[r for r in records if r['unique_literal_name_and_utc_match']]
    report={'verified_at':datetime.now(timezone.utc).isoformat(),'native_proposals_n':len(records),
            'unique_literal_name_and_utc_match_n':len(unique),
            'equal_season_year_label_n':sum(r['matches'][0]['season_year_label_equal'] for r in unique),
            'different_season_year_label_n':sum(not r['matches'][0]['season_year_label_equal'] for r in unique),
            'af_records_n':len(af['records']),'af_audit_sha256':hashlib.sha256(afpath.read_bytes()).hexdigest(),
            'frozen_manifest_sha256':manifest,'canonical_approved_n':0,'cross_provider_joins_n':0,
            'model_imported_n':0,'production_eligible':False,'records':records}
    with (root/'outputs/provider_concordance_20261009.json').open('x') as fh:json.dump(report,fh,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False))


if __name__=='__main__':main()
