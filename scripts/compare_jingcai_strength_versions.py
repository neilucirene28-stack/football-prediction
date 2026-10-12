"""只读比较两版本；所有新计算均标为历史重放，不写入赛前账本。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(project, records, mode):
    sys.path.insert(0, str(project.resolve()))
    from engine.jingcai_predictor import predict
    data = json.loads(records.read_text())
    output = []
    for row in data['results']:
        cfg = dict(row.get('config') or {})
        if mode == 'candidate':
            cfg['strength_baseline_mode'] = 'venue_normalized'
        result = predict(row['input'], cfg,
                         asof=datetime.fromisoformat(row['prediction']['prediction_generated_at']))
        assert result['evaluation_mode'] == 'historical_replay'
        assert result['status'] == 'ok' and result['project_scope'] == 'jingcai'
        output.append({'number': row['number'], 'prediction': result})
    print(json.dumps(output, ensure_ascii=False))


def summary(result):
    matrix = result['score_matrix_full']
    cells = [(h, a, v) for h, row in enumerate(matrix) for a, v in enumerate(row)]
    assert abs(sum(v for _, _, v in cells) - 1) < 1e-9
    ranked = sorted(cells, key=lambda x: (-x[2], x[0], x[1]))
    wdl = [sum(v for h, a, v in cells if h > a),
           sum(v for h, a, v in cells if h == a),
           sum(v for h, a, v in cells if h < a)]
    assert max(abs(a - b) for a, b in zip(wdl, result['p_final_full'])) < 1e-9
    goals = sum((h + a) * v for h, a, v in cells)
    assert abs(goals - result['derivatives']['expected_goals_full']) < 1e-9
    bands = [sum(v for h, a, v in cells if h + a <= 1),
             sum(v for h, a, v in cells if 2 <= h + a <= 3),
             sum(v for h, a, v in cells if h + a >= 4)]
    return {'lambda_home': result['lambda_home_full'], 'lambda_away': result['lambda_away_full'],
            'wdl': result['p_final_full'], 'direction': ('home', 'draw', 'away')[max(range(3), key=lambda i: wdl[i])],
            'expected_goals': goals, 'goal_bands': bands,
            'top5': [{'score': f'{h}-{a}', 'prob': v} for h, a, v in ranked[:5]],
            'model_version': result['model_version']}


def main(args):
    records = args.records.resolve()
    data = json.loads(records.read_text())
    assert data['project'] == 'jingcai'
    files = [records, *sorted((records.parent / 'sealed').glob('*.json'))]
    files += sorted(records.parent.glob('*.zip'))
    before = {str(p): sha(p) for p in files}
    script = Path(__file__).resolve()
    current = script.parents[1]
    versions = []
    for project, mode in ((args.reference_project.resolve(), 'reference'),
                          (current, 'default'), (current, 'candidate')):
        run = subprocess.run([sys.executable, str(script), '--worker', str(project), str(records), mode],
                             capture_output=True, text=True, check=True)
        versions.append(json.loads(run.stdout))
    reference, default, candidate = versions
    assert len(reference) == len(default) == len(candidate) == len(data['results'])
    paired = []
    for row, old, new in zip(data['results'], reference, candidate):
        assert row['number'] == old['number'] == new['number']
        seal = records.parent / 'sealed' / f"{row['number'][-3:]}-{row['match_id']}.json"
        assert sha(seal) == row['sealed_record_sha256']
        assert json.loads(seal.read_text())['prediction'] == row['prediction']
        original = row['prediction']
        for key in ('p_final_full', 'score_matrix_full', 'lambda_home_full', 'lambda_away_full'):
            assert old['prediction'][key] == original[key], f'reference mismatch: {row["number"]} {key}'
            assert default[len(paired)]['prediction'][key] == original[key], f'default mismatch: {row["number"]} {key}'
        old_s, new_s = summary(original), summary(new['prediction'])
        paired.append({'number': row['number'][-3:], 'home': row['input']['home'], 'away': row['input']['away'],
                       'kickoff_at': row['kickoff_at'], 'original_generated_at': original['prediction_generated_at'],
                       'sealed_record_sha256': row['sealed_record_sha256'], 'old': old_s, 'new': new_s,
                       'candidate_replay': new['prediction']})
    old_modes = Counter(r['old']['top5'][0]['score'] for r in paired)
    new_modes = Counter(r['new']['top5'][0]['score'] for r in paired)
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(), 'project': 'jingcai',
        'mode': 'historical_replay_comparison_not_new_live_forecasts',
        'qualification': 'No training or outcome data used. Numerical changes are not accuracy evidence.',
        'source_predictions_sha256': before[str(records)],
        'reference_replay_matches_original_matrices': True,
        'v13_default_matches_original_matrices': True,
        'candidate_config_overrides': {'strength_baseline_mode': 'venue_normalized'},
        'old_global_modes': old_modes, 'new_global_modes': new_modes,
        'direction_changes': [r['number'] for r in paired if r['old']['direction'] != r['new']['direction']],
        'largest_wdl_probability_change': max(abs(a - b) for r in paired for a, b in zip(r['old']['wdl'], r['new']['wdl'])),
        'old_expected_goals_range': [min(r['old']['expected_goals'] for r in paired), max(r['old']['expected_goals'] for r in paired)],
        'new_expected_goals_range': [min(r['new']['expected_goals'] for r in paired), max(r['new']['expected_goals'] for r in paired)],
        'observed_league_statistics_used': sum(r['candidate_replay']['input_audit']['league_baseline']['status'] == 'observed_league_statistics' for r in paired),
        'records': paired,
    }
    assert before == {str(p): sha(p) for p in files}
    report['original_files_unchanged'] = len(files)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'saved_22_input_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    names = {'home': '胜', 'draw': '平', 'away': '负'}
    lines = ['# 竞彩 v13：22场原输入只读比较', '',
             '本表使用2026-10-11已封存的v12真实赛前输入，以原生成时刻为评估截止进行历史重放。v13计算在现在实际完成，属于研究比较，没有新增或替换任何真实赛前预测，也未使用赛果训练。', '',
             'v12重放及v13默认兼容模式的完整比分矩阵、胜平负与进球强度逐场等于原记录。本表v13列明确开启venue_normalized候选模式；没有将候选自动升级为默认。不能根据概率变化判断更准。', '',
             f"胜平负首选发生变化的场次：{'、'.join(report['direction_changes']) or '无'}。单项胜平负概率最大变化{report['largest_wdl_probability_change'] * 100:.2f}个百分点。", '',
             f"本轮可应用的全联赛赛季统计数量为{report['observed_league_statistics_used']}；仍用明确标记的2.7球模型假设，未把两队战绩伪装为联赛均值。", '',
             '| 编号 | 对阵 | v12首选 | v13研究首选 | 预期总进球 v12→v13 | 4球及以上 v12→v13 | 总体最可能比分 v12→v13 |',
             '|---|---|---|---|---|---|---|']
    for r in paired:
        a, b = r['old'], r['new']
        pick = lambda s: f"{names[s['direction']]} {max(s['wdl']):.1%}"
        score = lambda s: f"{s['top5'][0]['score']}（{s['top5'][0]['prob']:.1%}）"
        lines.append(f"| {r['number']} | {r['home']}—{r['away']} | {pick(a)} | {pick(b)} | {a['expected_goals']:.2f}→{b['expected_goals']:.2f} | {a['goal_bands'][2]:.1%}→{b['goal_bands'][2]:.1%} | {score(a)}→{score(b)} |")
    lines += ['', f'v12总体比分众数计数：{dict(old_modes)}。',
              f'v13总体比分众数计数：{dict(new_modes)}。', '',
              '比分按全部比分的无条件概率排序，没有为减少重复而人为挑选比分。伤停、外援与红黄牌的新学习系数保持未启用。', '',
              f"原输入、原预测包与22个封存文件共{len(files)}个文件的字节摘要在比较前后保持一致。", '']
    (args.output / 'saved_22_input_comparison.md').write_text('\n'.join(lines))
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--records', type=Path, required=True)
        parser.add_argument('--reference-project', type=Path, required=True)
        parser.add_argument('--output', type=Path, required=True)
        main(parser.parse_args())
