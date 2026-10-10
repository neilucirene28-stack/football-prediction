"""Immutable local forecast files with SHA256 and actual durable-write receipts.

This witnesses local storage timing only. It is not an independently authenticated
source-availability record or proof that any supplied odds were available earlier.
"""
import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .jingcai_evaluation import dt


def _exclusive_write(path, data):
    with path.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def archive_prediction(payload, result, directory=None):
    if result.get('status') != 'ok' or result.get('model') != 'jingcai':
        raise ValueError('only successful Jingcai forecasts can be archived')
    timing=result.get('prediction_timing') or {}
    if timing.get('mode') != 'prospective':raise ValueError('historical replay is not a prospective archive')
    now=datetime.now(timezone.utc);kickoff=dt(payload['kickoff_at'])
    if not dt(payload['snapshot_at']) <= dt(timing['generated_at']) <= now < kickoff:
        raise ValueError('archive must occur before kickoff after genuine generation')
    if (result.get('match') or {}).get('kickoff_at') != payload['kickoff_at']:
        raise ValueError('fixture mismatch')
    root=Path(directory or os.environ.get('JINGCAI_ARCHIVE_DIR') or
              Path(__file__).resolve().parents[1]/'data/snapshots/jingcai')
    forecast_id=str(uuid.uuid4())
    folder=root/forecast_id;folder.mkdir(parents=True,exist_ok=False)
    data=json.dumps({'schema':'jingcai.prospective.v1','forecast_id':forecast_id,
                     'input':payload,'result':result},ensure_ascii=False,sort_keys=True,
                    allow_nan=False,separators=(',',':')).encode()
    _exclusive_write(folder/'forecast.json',data)
    for path in (folder,root):
        directory_fd=os.open(path,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    durable_at=datetime.now(timezone.utc)
    receipt={'schema':'jingcai.local-receipt.v1','forecast_id':forecast_id,
             'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
             'durable_at':durable_at.isoformat(),'kickoff_at':kickoff.isoformat(),
             'status':'saved_before_kickoff' if durable_at < kickoff else 'late_write',
             'independent_source_provenance_verified':False}
    _exclusive_write(folder/'receipt.json',json.dumps(receipt,ensure_ascii=False,sort_keys=True).encode())
    return receipt


def verify_archive(folder):
    folder=Path(folder);data=(folder/'forecast.json').read_bytes()
    receipt=json.loads((folder/'receipt.json').read_text())
    if hashlib.sha256(data).hexdigest()!=receipt['sha256'] or len(data)!=receipt['bytes']:
        raise ValueError('forecast bytes do not match receipt')
    forecast=json.loads(data)
    if receipt['status']!='saved_before_kickoff' or dt(receipt['durable_at']) >= dt(receipt['kickoff_at']):
        raise ValueError('archive was not durably stored before kickoff')
    if forecast['forecast_id']!=receipt['forecast_id']:
        raise ValueError('receipt identity mismatch')
    if dt(forecast['input']['kickoff_at'])!=dt(receipt['kickoff_at']):
        raise ValueError('receipt fixture mismatch')
    result=forecast['result'];timing=result.get('prediction_timing') or {}
    if result.get('model')!='jingcai' or timing.get('mode')!='prospective':
        raise ValueError('not a prospective Jingcai archive')
    if not dt(forecast['input']['snapshot_at']) <= dt(timing['started_at']) <= dt(timing['generated_at']) <= dt(receipt['durable_at']):
        raise ValueError('inconsistent archive clocks')
    probabilities=result['p_final_full']
    if len(probabilities)!=3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities) or abs(sum(probabilities)-1)>1e-8:
        raise ValueError('invalid archived probabilities')
    return forecast,receipt
