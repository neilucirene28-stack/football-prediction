"""Past-only research selection from authenticated earliest saved alternatives.

This never refits old forecasts or changes their originally selected parameter.
Snapshots retain original input bytes so a new freeze can replay its decision.
"""
from contextvars import ContextVar
import hashlib
import json
import math
from pathlib import Path
import tempfile

from .evaluation import _brier
from .snapshot import _datetime

MIN_SELECTION_MATCHES = 30
_active = ContextVar('feedback_verification_stack', default=())


def build_feedback(registry_path, registry_sha256, results_path, *, cutoff_at,
                   current_fixtures, root=None):
    from .frozen_settlement import load_bundle, NATIVE_IDS
    from .unique_settlement import resolve_bundle, settle_unique
    from .walk_forward import RIDGES
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    raw_registry = Path(registry_path).read_bytes()
    raw_results = Path(results_path).read_bytes()
    if hashlib.sha256(raw_registry).hexdigest() != registry_sha256:
        raise ValueError('feedback registry digest differs')
    cutoff = _datetime(cutoff_at, 'feedback.cutoff_at')
    registry = json.loads(raw_registry)
    # Strictly earlier archives prevent self-referential feedback chains.
    if any(_datetime(b['sealed_at'], 'feedback.sealed_at') >= cutoff for b in registry['bundles']):
        raise ValueError('feedback archive must be sealed before the new decision')
    all_keys = {(f['period'], f['match_id']) for f in current_fixtures}
    upcoming = [f for f in current_fixtures if _datetime(f['kickoff_at'], 'feedback.kickoff_at') > cutoff]
    current_keys = {(f['period'], f['match_id']) for f in upcoming}
    current_events = {f.get('provider_match_id') for f in upcoming
                      if f.get('provider_match_id') is not None}
    if len(all_keys) != len(current_fixtures):
        raise ValueError('duplicate current feedback fixture')
    rows = [json.loads(line) for line in raw_results.splitlines() if line.strip()]
    settled = settle_unique(registry_path, registry_sha256=registry_sha256,
                            results=rows, evaluated_at=cutoff_at, root=root)
    paired = {(r['period'], r['match_id']) for r in settled['paired_scores']}
    indexed = {(r['period'], r['match_id']): r for r in rows}
    eligible, excluded_current, cache = [], [], {}
    for entry in registry['rows']:
        identity = (entry['period'], entry['match_id'])
        if identity not in paired:
            continue
        # No result belonging to an upcoming forecast may enter its selection.
        # Expired blocked ledger rows can supply earlier genuine observations;
        # they receive no new probabilities and their old selection is retained.
        if identity in current_keys or entry['physical_identity'][0] in current_events:
            excluded_current.append(list(identity))
            continue
        key = (entry['bundle'], entry['manifest_sha256'])
        if key not in cache:
            report, pool, predictions = load_bundle(resolve_bundle(key[0], root),
                manifest_sha256=key[1], evaluated_at=cutoff_at)
            if (report['model_family'] != 'l1_team_strength' or
                    report['identity_mode'] != 'provider_native_espn' or
                    report['declared_candidates'] != list(RIDGES)):
                raise ValueError('feedback candidate family, identity or grid differs')
            cache[key] = pool, predictions
        pool, predictions = cache[key]
        fixture = pool[identity[1]]
        result = indexed[identity]
        if any(result[k] != fixture[k] for k in NATIVE_IDS):
            raise ValueError('feedback result native identity differs')
        pred = predictions[identity[1]]
        eligible.append({'period': identity[0], 'match_id': identity[1],
            'bundle': key[0], 'manifest_sha256': key[1], 'sealed_at': entry['sealed_at'],
            'result_available_at': max((t for t in (result['verified_at'], result['result_source']['available_at'],
                                      result.get('result_available_at')) if t is not None),
                                      key=lambda t: _datetime(t, 'feedback.result_clock')),
            'original_selected_parameter': pred['selected_parameter'],
            'candidate_brier': {str(k): _brier(pred['alternatives'][str(k)]['vectors']['wdl'],
                                result['ft_home'], result['ft_away']) for k in RIDGES}})
    losses = {str(k): math.fsum(r['candidate_brier'][str(k)] for r in eligible) / len(eligible)
              for k in RIDGES} if len(eligible) >= MIN_SELECTION_MATCHES else {}
    selected = min(RIDGES, key=lambda k: losses[str(k)]) if losses else None
    return {'schema': 'bd1-earliest-frozen-selection-feedback-1', 'cutoff_at': cutoff_at,
            'registry_sha256': registry_sha256, 'registry_original_utf8': raw_registry.decode('utf-8'),
            'results_sha256': hashlib.sha256(raw_results).hexdigest(),
            'results_original_utf8': raw_results.decode('utf-8'),
            'declared_candidates': list(RIDGES), 'min_selection_matches': MIN_SELECTION_MATCHES,
            'available_paired_n': len(paired), 'excluded_current_fixture_keys': excluded_current,
            'excluded_not_yet_available_result_n': len(rows) - len(paired),
            'eligible_rows': eligible, 'selection_n': len(eligible),
            'selected_parameter': selected, 'past_only_brier': losses,
            'policy': 'earliest_saved_alternatives_prior_decisions_wdl_brier_ties_favour_prior',
            'old_forecasts_refitted': False, 'old_forecasts_reselected': False,
            'production_gate_passed': False}


def verify_feedback(receipt, *, cutoff_at, current_fixtures, root=None):
    """Reproduce the receipt from pinned bytes and authenticated old bundles."""
    if not isinstance(receipt, dict) or receipt.get('cutoff_at') != cutoff_at:
        raise ValueError('feedback decision clock differs')
    key = (receipt.get('registry_sha256'), cutoff_at)
    stack = _active.get()
    if key in stack:
        raise ValueError('cyclic feedback archive dependency')
    token = _active.set(stack + (key,))
    try:
        with tempfile.TemporaryDirectory(prefix='bd1-feedback-') as temp:
            registry, results = Path(temp)/'registry.json', Path(temp)/'results.jsonl'
            registry.write_bytes(receipt['registry_original_utf8'].encode('utf-8'))
            results.write_bytes(receipt['results_original_utf8'].encode('utf-8'))
            if hashlib.sha256(results.read_bytes()).hexdigest() != receipt['results_sha256']:
                raise ValueError('feedback results digest differs')
            expected = build_feedback(registry, receipt['registry_sha256'], results,
                cutoff_at=cutoff_at, current_fixtures=current_fixtures, root=root)
        if receipt != expected:
            raise ValueError('feedback selection receipt differs from original frozen evidence')
        return receipt
    finally:
        _active.reset(token)
