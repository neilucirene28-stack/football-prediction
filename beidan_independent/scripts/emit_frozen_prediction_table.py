"""Render an existing sealed forecast; never refit or backdate predictions."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from beidan_bd1.frozen_settlement import load_bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--manifest-sha256', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--ledger', required=True)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    report, pool, predicted = load_bundle(args.bundle, manifest_sha256=args.manifest_sha256,
                          evaluated_at=datetime.now(timezone.utc).isoformat())
    roster = {r['match_id']:r for r in json.loads((root/'outputs/20261010_179_shadow.json').read_bytes())['predictions']}
    statuses = {r['match_id']:r for f in report['folds'] for r in f['matches']}
    rows = []
    by_league = {}
    for mid, fixture in pool.items():
        original = roster[mid]
        saved = statuses[mid]
        row = {k:original[k] for k in ('match_id','seq','league','home','away','kickoff_at')}
        row.update(status=saved['status'], reason=saved.get('reason'),
                   canonical_identity_approved=False, production_eligible=False,
                   provider_match_id=fixture.get('provider_match_id'))
        rows.append(row)
        group = by_league.setdefault(row['league'], Counter())
        group['offered_n'] += 1
        group['predicted_n' if mid in predicted else 'blocked_n'] += 1
    ledger = {'freeze_manifest_sha256':args.manifest_sha256, 'cutoff_at':report['evaluated_at'],
              'complete_pool_n':len(pool),'research_predicted_n':len(predicted),
              'blocked_n':len(pool)-len(predicted), 'canonical_approved_n':0,
              'production_eligible':False, 'by_league':{k:dict(v) for k,v in by_league.items()}, 'rows':rows}
    with Path(args.ledger).open('x') as fh: json.dump(ledger, fh, ensure_ascii=False, indent=2)
    percent=lambda v:'/'.join(f"{v[k]*100:.1f}%" for k in ('胜','平','负'))
    lines=['# 北单 2026-10-10 前瞻研究概率', '',
           f"真实封存报告生成时刻：{report['executed_at']}（UTC）。完整池 {len(pool)} 场，研究候选 {len(predicted)} 场，阻断 {len(pool)-len(predicted)} 场。", '',
           '当前已选 ridge=None，即 L3 冷启动根先验；L1 ridge=5 是预声明诊断候选，尚无实战校准证明。中文球队绑定全部未批准，让球/SP未导入。概率均为不让球胜平负，不能当作北单让球玩法的概率。', '',
           '| 场号 | 联赛 | 比赛 | 北京开球 | 已选 L3 胜/平/负 | L1 ridge=5 胜/平/负（诊断） | L1 前三比分（诊断） |',
           '|---|---|---|---|---|---|---|']
    for row in rows:
        if row['match_id'] not in predicted: continue
        saved=predicted[row['match_id']]; chosen=saved['candidate']['vectors']; diag=saved['alternatives']['5.0']['vectors']
        scores='、'.join(f"{k} ({p*100:.1f}%)" for k,p in sorted(diag['score'].items(),key=lambda kv:-kv[1])[:3])
        lines.append(f"| {row['seq']} | {row['league']} | {row['home']} vs {row['away']} | {row['kickoff_at']} | {percent(chosen['wdl'])} | {percent(diag['wdl'])} | {scores} |")
    lines.extend(['', '尚无成对已结算赛果，Brier 为空。旧冻结文件保留，新表只读取本次已封存的概率。', '',
                  f"独立保留的冻结清单 SHA256：`{args.manifest_sha256}`。", '',
                  '## 完整池缺口', '', '| 联赛 | 在售场数 | 研究候选 | 阻断 |', '|---|---|---|---|'])
    for league, values in by_league.items():
        lines.append(f"| {league} | {values['offered_n']} | {values['predicted_n']} | {values['blocked_n']} |")
    with Path(args.out).open('x') as fh: fh.write('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in ledger.items() if k not in ('by_league','rows')},ensure_ascii=False))


if __name__ == '__main__': main()
