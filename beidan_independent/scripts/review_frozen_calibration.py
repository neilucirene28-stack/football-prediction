"""Display descriptive calibration of saved earliest probabilities only."""
import argparse
import json
from pathlib import Path
from beidan_bd1.calibration_review import review_calibration


def write_review(value, json_out, md_out):
    targets = [Path(json_out), Path(md_out)]
    if targets[0].resolve() == targets[1].resolve() or any(p.exists() for p in targets):
        raise ValueError('calibration outputs must be new distinct files')
    labels = {'wdl': '不让球胜平负', 'total_goals': '总进球', 'odd_even': '上下单双',
              'score_31': '比分31类', 'half_full': '半全场', 'half_wdl': '半场胜平负（原联合概率边际）'}
    fmt = lambda x: f'{x:.5f}' if x is not None else '未计分'
    lines = ['# 北单最早封存概率的描述性校准检查', '',
             f"实际评估：{value['evaluated_at']}。完整池{value['offered_n']}场，唯一预测{value['unique_prediction_n']}场，已结算{value['paired_n']}场/{value['period_n']}期。", '',
             '只读取最早合格封存：正式已选模型与原先声明的L1 ridge=5诊断并列，不重拟合或赛后重选。', '',
             '| 模型 | 概率类别 | 样本n | Brier（除类别数K） | LogLoss | Top-label ECE | 置信度－命中率 |',
             '|---|---|---:|---:|---:|---:|---:|']
    for model, markets in value['metrics'].items():
        for market, m in markets.items():
            lines.append(f"| {'正式已选' if model == 'selected' else 'L1诊断'} | {labels[market]} | {m['n']} | {fmt(m['brier_category_mean'])} | {fmt(m['natural_logloss'])} | {fmt(m['top_label_ece'])} | {fmt(m['confidence_minus_accuracy'])} |")
    lines += ['', 'Brier按K个类别的平方误差平均；LogLoss为实际类别概率的负自然对数，不使用概率下限。若实际类别概率为0，JSON明确记录无限LogLoss及数量，有限均值为空。指标只比较同一类别体系，不能跨玩法直接排名。', '',
              'ECE使用固定10个等宽概率箱；空箱的经验命中率和区间保持空值。类别分别分箱的ECE、Top-label分箱及Wilson95%区间保留在JSON。Wilson区间未调整比赛之间的相关性；19场内分箱结果不是校准合格证明。缺半场的比赛不计入两个半场指标。', '',
              '| 模型 | 胜平负最高概率箱 | n | 平均置信度 | 实际命中率 | Wilson95% |',
              '|---|---|---:|---:|---:|---|']
    for model, markets in value['metrics'].items():
        for b in markets.get('wdl', {}).get('top_label_bins', []):
            if not b['n']: continue
            interval='—'.join(f'{x:.3f}' for x in b['wilson_95'])
            lines.append(f"| {'正式已选' if model == 'selected' else 'L1诊断'} | {b['lower']:.1f}至{b['upper']:.1f} | {b['n']} | {fmt(b['mean_probability'])} | {fmt(b['empirical_rate'])} | {interval} |")
    gate = value['minimum_sample_gate']
    lines += ['', f"最低独立样本门槛：{gate['min_settled_periods']}期/{gate['min_pairs']}场；目前满足：{gate['sample_threshold_met']}。不据本报告拟合校准器、重选正式模型或批准生产。", '',
              '官方让球与规范身份未批准，让球校准保持为空。尚未可得的赛果不计分，缺失标签不填零；原概率与冻结文件不改。']
    with targets[0].open('x') as stream: json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
    with targets[1].open('x') as stream: stream.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry', required=True)
    ap.add_argument('--registry-sha256', required=True)
    ap.add_argument('--results', required=True)
    ap.add_argument('--json-out', required=True)
    ap.add_argument('--markdown-out', required=True)
    args = ap.parse_args()
    value = review_calibration(args.registry, args.registry_sha256, args.results)
    write_review(value, args.json_out, args.markdown_out)
    print(json.dumps({k: value[k] for k in ('paired_n', 'period_n', 'excluded_not_yet_available_result_n', 'minimum_sample_gate')}))
