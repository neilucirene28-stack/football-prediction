#!/usr/bin/env python3
"""原件校验→竞彩专用适配器→新快照输入；不覆盖已封存预测。"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.jingcai_intelligence import (api_football, bind_sporttery_rows, collect,
                                         espn_summary, sportmonks_fixture, sporttery_table, premierinjuries_table)
from engine.jingcai_inputs import aware_datetime, validate_payload
from engine.jingcai_ledger import input_digest
from engine.jingcai_observations import SCHEMA, build_observations, match_identity, merge_same_source
from collector.jingcai_api_football import bundle as api_bundle, fixture_context


def enrich(payload, packets, *, refresh_snapshot=False, replace_observations=False):
    if not isinstance(payload, dict): raise ValueError('竞彩输入须为对象')
    if any(payload.get(k, 'jingcai') != 'jingcai' for k in ('project', 'model', 'project_scope')) or any(str(k).startswith('beidan') for k in payload):
        raise ValueError('竞彩情报导入拒绝其他项目输入')
    p = deepcopy(payload)
    if not isinstance(packets, list): raise ValueError('来源情报须为数组')
    previous = p.get('team_observations')
    retained = []
    if previous is not None and not replace_observations:
        build_observations(p, aware_datetime(p['snapshot_at'], 'snapshot_at'))
        retained = previous['packets']
    combined = deepcopy(retained + packets)
    match = combined[0]['match'] if combined else match_identity(p)
    p['team_observations'] = {'schema': SCHEMA, 'project': 'jingcai', 'match': match, 'packets': combined}
    if refresh_snapshot:
        now = datetime.now(timezone.utc)
        if aware_datetime(p['snapshot_at'], 'snapshot_at') > now:
            raise ValueError('原快照在未来')
        p['snapshot_at'] = now.isoformat()
        p['enrichment_previous_input_sha256'] = input_digest(payload)
        p['enrichment_generated_at'] = now.isoformat()
    cutoff = aware_datetime(p['snapshot_at'], 'snapshot_at')
    _, audit = validate_payload(p, cutoff)
    return p, audit['team_observations']


def import_raw(payload, raw, meta, binding, match, kind, *, player_bindings=None, fixture_raw=None, fixture_meta=None):
    if hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('情报原件字节摘要不匹配')
    if meta.get('status') != 'ok' or meta.get('http_status') != 200:
        raise ValueError('HTTP失败原件不能导入为情报正文')
    expected_source = {'espn': 'espn', 'api-lineups': 'api_football', 'api-injuries': 'api_football',
                       'api-players': 'api_football', 'sportmonks': 'sportmonks', 'sporttery': 'sporttery', 'premierinjuries': 'premierinjuries'}.get(kind)
    if expected_source and expected_source != meta.get('source'):
        raise ValueError('适配器与来源不匹配')
    if kind == 'sporttery':
        result = sporttery_table(raw.decode('utf-8'))
        packet = bind_sporttery_rows(result['rows'], meta, binding, match, player_bindings or [])
        packet['parse_status'] = result['status']
        if result['status'] == 'unsupported':
            packet.update(status='unsupported', reason=result.get('reason'))
    elif kind == 'espn': packet = espn_summary(json.loads(raw), meta, binding, match)
    elif kind.startswith('api-'):
        if fixture_raw is None or fixture_meta is None: raise ValueError('API情报导入须提供赛程原件及元数据')
        packet = import_api_pages([raw], [meta], fixture_raw, fixture_meta, binding, match, kind[4:])
    elif kind == 'sportmonks': packet = sportmonks_fixture(json.loads(raw), meta, binding, match)
    elif kind == 'premierinjuries': packet = premierinjuries_table(raw.decode('utf-8'), meta, binding, match, player_bindings or [])
    elif kind == 'normalized':
        packet = json.loads(raw)
        # 规范化原件是输入文件；evidence对应另行留存的网页/API原件，不能用输入文件自证。
        raise ValueError('normalized请使用--packets与--evidence-files逐一核验来源原件')
    else: raise ValueError('未知来源适配器')
    return packet


def import_api_pages(raws, metas, fixture_raw, fixture_meta, binding, match, kind='players'):
    if len(raws) != len(metas): raise ValueError('分页原件和元数据数量不同')
    for raw, m in [(fixture_raw, fixture_meta), *zip(raws, metas)]:
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != m.get('sha256'):
            raise ValueError('API原件字节摘要不匹配')
    return api_bundle(json.loads(fixture_raw), fixture_meta, [json.loads(raw) for raw in raws], metas, binding, match, kind)


def verify_packets(packets, files):
    expected = set()
    for i, packet in enumerate(packets):
        for e in packet['evidence']:
            key = f"{i}:{e['id']}"; expected.add(key)
            if key not in files or hashlib.sha256(Path(files[key]).read_bytes()).hexdigest() != e['sha256']:
                raise ValueError('来源原件缺失或摘要不匹配:' + key)
    if set(files) != expected:
        raise ValueError('来源原件须与证据一一对应')
    for i, packet in enumerate(packets):
        if packet.get('source') == 'api_football' and packet.get('status') == 'ok':
            ctx = packet.get('fixture_context') or {}
            key = f"{i}:{ctx.get('evidence_id')}"
            if key not in files: raise ValueError('API规范化包缺赛程原件')
            rebuilt = fixture_context(json.loads(Path(files[key]).read_bytes()), packet['binding'], packet['match'], evidence_id=ctx['evidence_id'])
            if rebuilt != ctx: raise ValueError('API声明赛程上下文与原件不一致')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--payload', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--refresh-snapshot', action='store_true', help='建立当前的新输入快照；不修改旧文件、不代表已生成预测')
    cli.add_argument('--merge-same-source', action='store_true', help='按同源同fixture同球员ID合并；字段冲突拒绝')
    cli.add_argument('--replace-observations', action='store_true', help='在新输入中替换已有情报包；默认保留并追加，不修改旧输入文件')
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument('--collect', choices=['sporttery', 'espn', 'api_football', 'premierinjuries'])
    mode.add_argument('--raw', type=Path)
    mode.add_argument('--packets', type=Path, help='JSON数组；所有登记来源均可经规范化原件导入')
    mode.add_argument('--api-pages', type=Path, help='完整API分页清单JSON数组，每项raw/meta路径相对于清单文件目录')
    cli.add_argument('--meta', type=Path)
    cli.add_argument('--binding', type=Path, help='JSON对象: match, binding, player_bindings(可选)')
    cli.add_argument('--kind', choices=['sporttery', 'espn', 'api-lineups', 'api-injuries', 'api-players', 'sportmonks', 'premierinjuries'])
    cli.add_argument('--capture-dir', type=Path)
    cli.add_argument('--evidence-files', type=Path)
    cli.add_argument('--fixture-raw', type=Path, help='API赛程原件（离线API导入必需）')
    cli.add_argument('--fixture-meta', type=Path)
    cli.add_argument('--max-api-pages', type=int, default=10, help='单队最多统计页数；不包含赛程请求，默认10')
    args = cli.parse_args()
    payload = json.loads(args.payload.read_text())
    if args.output.exists(): cli.error('输出已存在，不能覆盖旧输入或预测')
    if args.packets:
        if not args.evidence_files: cli.error('规范化导入须提供--evidence-files')
        packets = json.loads(args.packets.read_text()); verify_packets(packets, json.loads(args.evidence_files.read_text()))
    else:
        if not args.binding: cli.error('须提供显式比赛/球队映射')
        b = json.loads(args.binding.read_text())
        if args.api_pages:
            if not args.fixture_raw or not args.fixture_meta: cli.error('API分页导入须提供赛程原件和元数据')
            if args.kind not in (None, 'api-players'): cli.error('--api-pages仅用于球员统计')
            page_entries = json.loads(args.api_pages.read_text())
            if not isinstance(page_entries, list) or not page_entries: cli.error('分页清单须为非空数组')
            if len(page_entries) > args.max_api_pages: cli.error('分页清单超过max-api-pages')
            raws = [(args.api_pages.parent / e['raw']).read_bytes() for e in page_entries]
            metas = [json.loads((args.api_pages.parent / e['meta']).read_text()) for e in page_entries]
            packets = [import_api_pages(raws, metas, args.fixture_raw.read_bytes(), json.loads(args.fixture_meta.read_text()), b['binding'], b['match'])]
        elif args.collect:
            if not args.capture_dir: cli.error('采集须指定新的--capture-dir')
            kind = args.kind[4:] if args.kind and args.kind.startswith('api-') else None
            packets = [collect(args.collect, b['binding'], b['match'], args.capture_dir, kind=kind, key=os.environ.get('API_FOOTBALL_KEY'), player_bindings=b.get('player_bindings'), max_api_pages=args.max_api_pages)]
        else:
            if not args.meta or not args.kind: cli.error('原件导入须有--meta和--kind')
            packets = [import_raw(payload, args.raw.read_bytes(), json.loads(args.meta.read_text()), b['binding'], b['match'], args.kind, player_bindings=b.get('player_bindings'),
                      fixture_raw=args.fixture_raw.read_bytes() if args.fixture_raw else None,
                      fixture_meta=json.loads(args.fixture_meta.read_text()) if args.fixture_meta else None)]
    # 合并前逐包校验，避免合并掩盖未知证据、ID重复或名单矛盾。
    enriched, report = enrich(payload, packets, refresh_snapshot=args.refresh_snapshot, replace_observations=args.replace_observations)
    if args.merge_same_source:
        packets = merge_same_source(enriched['team_observations']['packets'])
        enriched, report = enrich(enriched, packets, replace_observations=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as handle:
        os.chmod(args.output, 0o600); handle.write(json.dumps(enriched, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'sources': [r['source'] for r in report.get('sources', [])],
                      'probability_effect_enabled': False, 'created_input_only_not_sealed_forecast': True}, ensure_ascii=False))


if __name__ == '__main__': main()
