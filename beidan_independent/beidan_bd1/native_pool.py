"""Combine audited native batches without promoting proposed CN bindings.

Archives checked today may be used prospectively, never backdated to their
match dates. A provider event may have only one proposed Beidan pool binding.
"""
from .snapshot import _datetime


def collect_batches(batches, *, cutoff_at):
    cutoff = _datetime(cutoff_at, 'cutoff_at')
    history, bindings, research, seen = {}, {}, {}, {}
    for batch in batches:
        clock = batch['verified_at']
        if _datetime(clock, 'verified_at') > cutoff:
            raise ValueError('batch is not available at cutoff')
        records = [item['record'] for item in batch['results']]
        if not records:
            raise ValueError('batch has no audited history')
        stages = {(r['provider_league_id'], r['season_year'], r['season_type']) for r in records}
        if len(stages) != 1:
            raise ValueError('batch mixes native league or season stages')
        stage = next(iter(stages))
        for row in records:
            if _datetime(row['verified_at'], 'row verified_at') > cutoff:
                raise ValueError('history not available at cutoff')
            key = row['match_id']
            if key in history and history[key] != row:
                raise ValueError('conflicting duplicate native history')
            history[key] = row
        for item in batch['research_predictions']:
            seq = item['seq']
            binding = item['binding']
            fixture_stage = tuple(binding.get(k) for k in
                                  ('provider_league_id', 'season_year', 'season_type'))
            if (not isinstance(fixture_stage[0], str) or not fixture_stage[0].isdigit()
                    or any(isinstance(v, bool) or not isinstance(v, int) or v < 1
                           for v in fixture_stage[1:]) or fixture_stage != stage):
                raise ValueError('fixture and history native league or season stages differ')
            eid = binding['proposed_provider_event_id']
            if seq in bindings or eid in seen:
                raise ValueError('duplicate pool sequence or provider fixture binding')
            if (binding.get('beidan_fixture_binding_approved') is True
                    or binding.get('canonical_identity_approved') is True):
                raise ValueError('native research must not claim canonical approval')
            seen[eid] = seq
            bindings[seq] = {'binding': binding, 'available_at': clock,
                             'provider_league_id': stage[0], 'season_year': stage[1],
                             'season_type': stage[2]}
            if item['status'] == 'native_research_binding_unapproved':
                research[seq] = item
    return list(history.values()), bindings, research
