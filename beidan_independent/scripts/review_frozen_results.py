"""Read earliest sealed vectors for descriptive post-match review, without fitting."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from beidan_bd1.frozen_settlement import load_bundle
from beidan_bd1.unique_settlement import resolve_bundle, settle_unique


def review(registry_path, digest, results_path, out_json, out_md):
    raw = Path(results_path).read_bytes()
    results = [json.loads(line) for line in raw.splitlines() if line.strip()]
    now = datetime.now(timezone.utc).isoformat()
    # Authenticate registry, reconstruct earliest ownership, reject duplicate results.
    settlement = settle_unique(registry_path, registry_sha256=digest, results=results, evaluated_at=now)
    registry = json.loads(Path(registry_path).read_bytes())
    entries = {row['match_id']: row for row in registry['rows']}
    root = Path(__file__).resolve().parents[1]
    caches, rows = {}, []
    for result in sorted(results, key=lambda row: row['seq']):
        mid = result['match_id']
        entry = entries[mid]
        key = (entry['bundle'], entry['manifest_sha256'])
        if key not in caches:
            report, _, predictions = load_bundle(resolve_bundle(key[0], root), manifest_sha256=key[1], evaluated_at=now)
            if report['model_family'] != 'l1_team_strength':
                raise ValueError('L1 diagnostic review requires L1 team-strength freeze')
            caches[key] = predictions
        pred = caches[key][mid]
        actual = '胜' if result['ft_home'] > result['ft_away'] else '负' if result['ft_home'] < result['ft_away'] else '平'
        selected = pred['candidate']['vectors']
        diagnostic = pred['alternatives']['5.0']['vectors']
        pick = max(selected['wdl'], key=selected['wdl'].get)
        dpick = max(diagnostic['wdl'], key=diagnostic['wdl'].get)
        score = f"{result['ft_home']}-{result['ft_away']}"
        top3 = sorted(diagnostic['score'], key=diagnostic['score'].get, reverse=True)[:3]
        brier = lambda v: math.fsum((v['wdl'][k] - (k == actual)) ** 2 for k in ('胜','平','负')) / 3
        mean = lambda v: math.fsum(sum(map(int, k.split('-'))) * p for k, p in v['score'].items())
        rows.append({'match_id': mid, 'seq': result['seq'], 'league': result['league'],
                     'home': result['home'], 'away': result['away'], 'ft': score,
                     'ht': f"{result['ht_home']}-{result['ht_away']}", 'actual_wdl': actual,
                     'selected_ridge': pred['selected_ridge'], 'selected_wdl_pick': pick,
                     'selected_wdl_hit': pick == actual, 'selected_brier': brier(selected),
                     'diagnostic_ridge': 5, 'diagnostic_wdl_pick': dpick,
                     'diagnostic_wdl_hit': dpick == actual, 'diagnostic_brier': brier(diagnostic),
                     'diagnostic_top3': top3, 'diagnostic_top3_hit': score in top3,
                     'selected_goal_error': mean(selected) - result['ft_home'] - result['ft_away'],
                     'diagnostic_goal_error': mean(diagnostic) - result['ft_home'] - result['ft_away'],
                     'diagnostic_probability_of_actual_wdl': diagnostic['wdl'][actual],
                     'original_manifest_sha256': key[1]})
    stats = {}
    groups = [('all', rows)] + [(league, [r for r in rows if r['league'] == league])
                                for league in sorted({r['league'] for r in rows})]
    for label, subset in groups:
        n = len(subset)
        stats[label] = {'n': n, 'selected_hits': sum(r['selected_wdl_hit'] for r in subset),
                       'diagnostic_hits': sum(r['diagnostic_wdl_hit'] for r in subset),
                       'diagnostic_score_top3_hits': sum(r['diagnostic_top3_hit'] for r in subset),
                       **{key: math.fsum(r[key] for r in subset) / n for key in
                          ('selected_brier', 'diagnostic_brier', 'selected_goal_error', 'diagnostic_goal_error')}}
    if not math.isclose(stats['all']['selected_brier'], settlement['brier_candidate'], abs_tol=1e-14):
        raise ValueError('descriptive review differs from authenticated unique settlement')
    value = {'label': 'earliest_frozen_selected_and_predeclared_diagnostic_review_no_reselection',
             'executed_at': now, 'registry_sha256': digest, 'results_sha256': hashlib.sha256(raw).hexdigest(),
             'execution_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'stats': stats, 'rows': rows, 'refitted': False, 'reselected_after_results': False,
             'production_gate_passed': False}
    lines = ['# 北单首批已核验赛果复盘', '', f'核验截止：{now} UTC。每场只读最早合格赛前封存；正式所选与 L1 ridge=5 诊断并列，不赛后重选。', '',
             '| 样本 | 已选方向命中 | L1 诊断方向命中 | L1 诊断 Top3 比分命中 | 已选 Brier | L1 诊断 Brier |',
             '|---|---|---|---|---|---|']
    for label, s in stats.items():
        lines.append(f"| {label} | {s['selected_hits']}/{s['n']} | {s['diagnostic_hits']}/{s['n']} | {s['diagnostic_score_top3_hits']}/{s['n']} | {s['selected_brier']:.5f} | {s['diagnostic_brier']:.5f} |")
    lines += ['', '方向命中为每场最高概率类别计数。Brier 为三个类别的平方误差平均，再按比赛取平均，越低越好。比分 Top3 从原封存的完整网格提取。', '',
              '| 场号 | 比赛 | FT | HT | 已选方向 | L1 诊断方向 | L1 诊断 Top3 |', '|---|---|---|---|---|---|---|']
    mark = lambda hit: '✓' if hit else '×'
    for r in rows:
        lines.append(f"| {r['seq']} | {r['home']}—{r['away']} | {r['ft']} | {r['ht']} | {r['selected_wdl_pick']} {mark(r['selected_wdl_hit'])} | {r['diagnostic_wdl_pick']} {mark(r['diagnostic_wdl_hit'])} | {', '.join(r['diagnostic_top3'])} {mark(r['diagnostic_top3_hit'])} |")
    lines += ['', f"所选进球均值误差（预测－实际）：{stats['all']['selected_goal_error']:+.5f} 球；L1 诊断：{stats['all']['diagnostic_goal_error']:+.5f} 球。均值误差不能用比分众数替代；本小样本不证明长期无偏。", '',
              f"完整分母 {settlement['offered_n']} 场，唯一预测 {settlement['predicted_n']} 场，未覆盖 {settlement['blocked_n']} 场；已结算 {settlement['paired_n']} 场，待结算 {len(settlement['pending_ids'])} 场。", '',
              '当前不足 5 期/500 唯一成对赛果，不据此选模型或认定生产达标。中文身份未批准、官方让球/SP未导入，不报告北单让球命中或ROI。']
    with Path(out_json).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
    with Path(out_md).open('x') as stream:
        stream.write('\n'.join(lines) + '\n')
    return stats


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry', required=True)
    ap.add_argument('--registry-sha256', required=True)
    ap.add_argument('--results', required=True)
    ap.add_argument('--json-out', required=True)
    ap.add_argument('--markdown-out', required=True)
    args = ap.parse_args()
    print(json.dumps(review(args.registry, args.registry_sha256, args.results,
                            args.json_out, args.markdown_out), ensure_ascii=False))
