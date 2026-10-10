"""Descriptive calibration of earliest sealed vectors; never fit a calibrator."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from .frozen_settlement import load_bundle
from .unique_settlement import resolve_bundle, settle_unique

EDGES = tuple(i / 10 for i in range(11))


def _bin(probability):
    # Decimal boundaries belong to the upper bin; p=1 stays in the last bin.
    return max(i for i in range(10) if EDGES[i] <= probability)


def _wilson(hits, n):
    if not n:
        return None
    z = 1.959963984540054
    rate = hits / n
    scale = 1 + z * z / n
    center = (rate + z * z / (2 * n)) / scale
    radius = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / scale
    return [max(0., center - radius), min(1., center + radius)]


def summarize_probabilities(observations, *, labels):
    """Fixed bins, category-mean Brier, natural-log loss without probability floors."""
    labels = tuple(labels)
    if len(labels) < 2 or len(labels) != len(set(labels)):
        raise ValueError('calibration labels must be distinct categories')
    rows = list(observations)
    identities = [(r['period'], r['match_id']) for r in rows]
    if len(identities) != len(set(identities)):
        raise ValueError('duplicate calibration event')
    for row in rows:
        values = row['probabilities']
        if (set(values) != set(labels) or row['actual'] not in labels or
                any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                    or not 0 <= v <= 1 for v in values.values()) or
                abs(math.fsum(values.values()) - 1) > 1e-8):
            raise ValueError('invalid calibration probabilities or actual category')
    n = len(rows)
    confidence = [(max(labels, key=r['probabilities'].get), r) for r in rows]

    def bins_for(items):
        bins = []
        for i in range(10):
            values = [(p, hit) for p, hit in items if _bin(p) == i]
            count = len(values)
            hits = sum(hit for _, hit in values)
            mean = math.fsum(p for p, _ in values) / count if count else None
            empirical = hits / count if count else None
            bins.append({'lower': EDGES[i], 'upper': EDGES[i + 1], 'upper_inclusive': i == 9,
                'n': count, 'hits': hits, 'mean_probability': mean, 'empirical_rate': empirical,
                'absolute_gap': abs(mean - empirical) if count else None,
                'wilson_95': _wilson(hits, count)})
        return bins

    top = bins_for([(r['probabilities'][pick], pick == r['actual']) for pick, r in confidence])
    classes = {label: bins_for([(r['probabilities'][label], r['actual'] == label) for r in rows])
               for label in labels}
    ece = lambda bins: math.fsum(b['n'] * b['absolute_gap'] for b in bins if b['n']) / n if n else None
    actual_probs = [r['probabilities'][r['actual']] for r in rows]
    zero_n = sum(p == 0 for p in actual_probs)
    accuracy = sum(pick == r['actual'] for pick, r in confidence) / n if n else None
    mean_confidence = math.fsum(r['probabilities'][pick] for pick, r in confidence) / n if n else None
    return {'n': n, 'category_n': len(labels), 'labels': labels,
        'brier_category_mean': math.fsum(math.fsum((r['probabilities'][k] - (k == r['actual'])) ** 2
                              for k in labels) / len(labels) for r in rows) / n if n else None,
        'natural_logloss': math.fsum(-math.log(p) for p in actual_probs) / n if n and not zero_n else None,
        'zero_actual_probability_n': zero_n, 'infinite_logloss': bool(zero_n),
        'accuracy': accuracy, 'mean_top_confidence': mean_confidence,
        'confidence_minus_accuracy': mean_confidence - accuracy if n else None,
        'top_label_ece': ece(top),
        'mean_classwise_ece': math.fsum(ece(bins) for bins in classes.values()) / len(labels) if n else None,
        'top_label_bins': top, 'classwise_bins': classes, 'probability_floor_applied': False}


def review_calibration(registry_path, digest, results_path, *, evaluated_at=None, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    now = evaluated_at or datetime.now(timezone.utc).isoformat()
    code = {name: hashlib.sha256((Path(__file__).resolve().parent / name).read_bytes()).hexdigest()
            for name in ('calibration_review.py', 'unique_settlement.py', 'frozen_settlement.py')}
    raw = Path(results_path).read_bytes()
    results = [json.loads(line) for line in raw.splitlines() if line.strip()]
    settlement = settle_unique(registry_path, registry_sha256=digest, results=results, evaluated_at=now, root=root)
    paired = {(r['period'], r['match_id']) for r in settlement['paired_scores']}
    registry = json.loads(Path(registry_path).read_bytes())
    entries = {(r['period'], r['match_id']): r for r in registry['rows']}
    caches, provenance = {}, []
    observations = {model: {} for model in ('selected', 'diagnostic_ridge_5')}
    for result in results:
        identity = (result['period'], result['match_id'])
        if identity not in paired:
            continue  # Actual availability is enforced by authenticated settlement.
        entry = entries[identity]
        key = (entry['bundle'], entry['manifest_sha256'])
        if key not in caches:
            report, _, predictions = load_bundle(resolve_bundle(key[0], root), manifest_sha256=key[1], evaluated_at=now)
            if report['model_family'] != 'l1_team_strength' or 5. not in report['declared_candidates']:
                raise ValueError('diagnostic ridge 5 was not declared in the original freeze')
            caches[key] = predictions
        pred = caches[key][identity[1]]
        h, a = result['ft_home'], result['ft_away']
        outcome = lambda x, y: '胜' if x > y else '负' if x < y else '平'
        wdl, total = outcome(h, a), h + a
        half = outcome(result['ht_home'], result['ht_away']) if result.get('ht_home') is not None else None
        provenance.append({'period': identity[0], 'match_id': identity[1], 'bundle': key[0],
                           'manifest_sha256': key[1], 'selected_parameter': pred['selected_parameter']})
        for model, value in [('selected', pred['candidate']), ('diagnostic_ridge_5', pred['alternatives']['5.0'])]:
            v = value['vectors']
            exact = f'{h}-{a}'
            score_label = exact if exact in value['score_31'] else wdl + '其他'
            markets = [('wdl', v['wdl'], wdl), ('total_goals', v['total_goals'], str(total) if total < 7 else '7+'),
                       ('odd_even', v['odd_even'], ('上' if total >= 3 else '下') + ('单' if total % 2 else '双')),
                       ('score_31', value['score_31'], score_label), ('half_full', v['half_full'], half + wdl if half else None),
                       ('half_wdl', {k: math.fsum(p for label, p in v['half_full'].items() if label[0] == k)
                                    for k in ('胜', '平', '负')}, half)]
            for market, probabilities, actual in markets:
                group = observations[model].setdefault(market, {'labels': list(probabilities), 'rows': []})
                if actual is not None:
                    group['rows'].append({'period': identity[0], 'match_id': identity[1],
                                          'probabilities': probabilities, 'actual': actual})
    metrics = {model: {market: summarize_probabilities(group['rows'], labels=group['labels'])
                      for market, group in groups.items()} for model, groups in observations.items()}
    if paired and not math.isclose(metrics['selected']['wdl']['brier_category_mean'],
                                  settlement['brier_candidate'], abs_tol=1e-14):
        raise ValueError('calibration Brier differs from authenticated earliest settlement')
    return {'label': 'earliest_frozen_descriptive_calibration_no_fit_or_reselection', 'evaluated_at': now,
            'executed_at': datetime.now(timezone.utc).isoformat(), 'registry_sha256': digest,
            'results_sha256': hashlib.sha256(raw).hexdigest(), 'execution_code_sha256': code,
            'offered_n': settlement['offered_n'], 'unique_prediction_n': settlement['predicted_n'],
            'paired_n': len(paired), 'excluded_not_yet_available_result_n': len(results) - len(paired),
            'period_n': len({period for period, _ in paired}), 'fixed_probability_bin_edges': EDGES,
            'metrics': metrics, 'original_forecast_provenance': provenance,
            'minimum_sample_gate': {k: settlement['numerical_gate'][k] for k in
                                   ('min_settled_periods', 'min_pairs', 'sample_threshold_met')},
            'handicap_calibration': None, 'handicap_reason': 'official_line_and_identity_unapproved',
            'refitted': False, 'reselected_after_results': False, 'production_gate_passed': False,
            'limitations': ['descriptive_fixed_bin_metrics_not_calibration_proof',
                           'wilson_bins_ignore_between_match_dependence', 'no_cross_market_metric_ranking',
                           'source_and_clock_not_independently_authenticated']}
