"""竞彩多源球员情报校验与特征；不同来源球员ID不按姓名合并。"""
from copy import deepcopy
from urllib.parse import urlsplit, parse_qsl

from .jingcai_inputs import aware_datetime, finite_number
from .jingcai_team_intelligence import checked_evidence, count, digest, text

SCHEMA = 'jingcai-source-observations-v1'
SOURCE_HOSTS = {
    'sporttery': ('sporttery.cn',),
    'espn': ('espn.com',),
    'api_football': ('api-sports.io', 'api-football.com'),
    'sportmonks': ('sportmonks.com',),
    'premierinjuries': ('premierinjuries.com',),
    'jleague': ('jleague.jp', 'j-league.or.jp'),
    'kleague': ('kleague.com',),
    'premierleague': ('premierleague.com', 'thefa.com'),
    'mls': ('mlssoccer.com',),
    'cfa': ('thecfa.cn', 'cfa.cn'),
    'transfermarkt': ('transfermarkt.com',),
    'opta': ('statsperform.com',),
    'wyscout': ('wyscout.com',),
    'statsbomb': ('statsbomb.com', 'github.com'),
    'fotmob_authorized': ('fotmob.com',),
}
PRIORITY = {s: 20 for s in SOURCE_HOSTS}
PRIORITY.update(sporttery=0, api_football=30, sportmonks=30, espn=40,
                premierinjuries=40, transfermarkt=50, fotmob_authorized=50,
                opta=30, wyscout=30, statsbomb=50)
STATS = ('season_minutes', 'season_goals', 'season_assists', 'season_yellow', 'season_red')
FIELDS = ('home', 'away', 'competition', 'season', 'kickoff_at')


def match_identity(payload):
    return {k: payload.get(k) for k in FIELDS}


def check_match(match, payload):
    if not isinstance(match, dict):
        raise ValueError('情报缺比赛绑定')
    for key in FIELDS[:-1]:
        text(match.get(key), 'match.' + key)
        if payload.get(key) is not None and match[key] != payload[key]:
            raise ValueError('情报比赛/赛季不匹配:' + key)
    if aware_datetime(match.get('kickoff_at'), 'match.kickoff_at') != aware_datetime(payload.get('kickoff_at'), 'kickoff_at'):
        raise ValueError('情报开球时间不匹配')


def _future(value, cutoff):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ('available_at', 'collected_at', 'published_at', 'feature_as_of') and v is not None:
                if aware_datetime(v, k) > cutoff:
                    raise ValueError('情报晚于快照截止，存在未来泄漏')
            _future(v, cutoff)
    elif isinstance(value, list):
        for v in value:
            _future(v, cutoff)


def _team(team, side, packet, evidence, payload):
    if not isinstance(team, dict):
        raise ValueError('情报球队须为对象')
    if team.get('team_id') != packet['binding'][side + '_id']:
        raise ValueError('情报原生球队ID不匹配')
    namespace = text(team.get('player_id_namespace'), 'player_id_namespace')
    if namespace != packet['source']:
        raise ValueError('球员ID命名空间与情报来源不一致')
    if not isinstance(team.get('roster_complete'), bool):
        raise ValueError('须声明完整名单状态')
    rows = team.get('players')
    if not isinstance(rows, list):
        raise ValueError('players须为列表')
    players = {}
    def proof(pid):
        if pid not in evidence:
            raise ValueError('球员情报引用未知证据')
    for p in rows:
        if not isinstance(p, dict):
            raise ValueError('球员记录须为对象')
        key = text(p.get('player_id'), 'player_id')
        if key in players:
            raise ValueError('重复球员ID')
        text(p.get('name'), 'player.name')
        if p.get('position') not in ('GK', 'DEF', 'MID', 'FWD', 'unknown'):
            raise ValueError('位置无效')
        status = p.get('availability', 'unknown')
        if status not in ('available', 'injured', 'suspended', 'doubtful', 'unknown'):
            raise ValueError('可用状态无效')
        if status != 'unknown':
            proof(p.get('availability_evidence_id'))
        for k in STATS:
            if p.get(k) is not None:
                count(p[k], k)
                proof(p.get('season_stats_evidence_id'))
                if p.get('season_stats_scope') != {k: packet['match'][k] for k in ('competition', 'season')}:
                    raise ValueError('球员统计赛事/赛季不匹配')
        reg = p.get('registration')
        if reg is not None:
            if not isinstance(reg, dict):
                raise ValueError('外援注册须为对象')
            if reg.get('category') not in ('foreign', 'domestic', 'exempt', 'partner', 'unknown'):
                raise ValueError('外援注册类别无效')
            quota = reg.get('counts_toward_foreign_quota')
            if quota is not None and not isinstance(quota, bool):
                raise ValueError('外援名额标记须为布尔值或null')
            if reg['category'] != 'unknown' or quota is not None:
                proof(reg.get('evidence_id'))
                text(reg.get('rule_version'), 'registration.rule_version')
                if reg.get('scope') != {**{k: packet['match'][k] for k in ('competition', 'season')}, 'team': payload[side]}:
                    raise ValueError('外援注册作用域不匹配')
        if p.get('anticipated_minutes') is not None:
            finite_number(p['anticipated_minutes'], 'anticipated_minutes', minimum=0, maximum=90)
            proof(p.get('anticipated_minutes_evidence_id'))
            if status in ('injured', 'suspended') and p['anticipated_minutes'] != 0:
                raise ValueError('确认缺阵不能有预计分钟')
        players[key] = p
    if team['roster_complete'] and len(players) < 11:
        raise ValueError('完整球队名单不足11人')
    lineup = team.get('lineup')
    starts = []; bench = []; lineup_status = 'unknown'
    if lineup is not None:
        if not isinstance(lineup, dict) or lineup.get('status') not in ('expected', 'confirmed'):
            raise ValueError('首发确认状态无效')
        proof(lineup.get('evidence_id'))
        starts, bench = lineup.get('starting_ids'), lineup.get('bench_ids')
        if not isinstance(starts, list) or not isinstance(bench, list):
            raise ValueError('首发/替补必须为列表')
        if any(not isinstance(x, str) or x not in players for x in starts + bench):
            raise ValueError('首发名单中有未知球员')
        if len(set(starts + bench)) != len(starts + bench):
            raise ValueError('首发替补重复')
        if len(starts) > 11 or (lineup['status'] == 'confirmed' and len(starts) != 11):
            raise ValueError('确认首发须有11人')
        if any(players[x].get('availability') in ('injured', 'suspended') for x in starts + bench):
            raise ValueError('名单与确认缺阵冲突')
        lineup_status = lineup['status']
    absent = [p for p in rows if p.get('availability') in ('injured', 'suspended')]
    complete = team['roster_complete']
    def total(field):
        return sum(p[field] for p in rows) if complete and all(p.get(field) is not None for p in rows) else None
    minutes = total('season_minutes'); yellow = total('season_yellow'); red = total('season_red')
    observed_total = lambda field: sum(p[field] for p in rows) if rows and all(p.get(field) is not None for p in rows) else None
    # 名单完整和全部可用性已核查，才能计算全队贡献占比。
    known_avail = complete and all(p.get('availability') in ('available', 'injured', 'suspended') for p in rows)
    def share(field):
        t = total(field)
        return sum(p[field] for p in absent) / t if known_avail and t else None
    registered = all((p.get('registration') or {}).get('category', 'unknown') != 'unknown' for p in rows)
    known_foreign = lambda p: (p.get('registration') or {}).get('category') == 'foreign'
    fstarts = sum(known_foreign(players[x]) for x in starts)
    all_starts_reg = len(starts) == 11 and all((players[x].get('registration') or {}).get('category', 'unknown') != 'unknown' for x in starts)
    quota_known = len(starts) == 11 and all(isinstance((players[x].get('registration') or {}).get('counts_toward_foreign_quota'), bool) for x in starts)
    anticipated = None
    if sum(p.get('anticipated_minutes') or 0 for p in rows) > 990 + 1e-8:
        raise ValueError('预计总分钟超过990')
    if complete and registered and all(p.get('anticipated_minutes') is not None for p in rows):
        if sum(p['anticipated_minutes'] for p in rows) > 990 + 1e-8:
            raise ValueError('预计总分钟超过990')
        anticipated = sum(p['anticipated_minutes'] for p in rows if known_foreign(p))
    return {'team': payload[side], 'team_id': team['team_id'], 'player_id_namespace': namespace,
            'roster_complete': complete, 'observed_players_n': len(rows),
            'observed_players': deepcopy(rows),
            'coverage': {'availability_known_n': sum(p.get('availability', 'unknown') != 'unknown' for p in rows),
                'registration_known_n': sum((p.get('registration') or {}).get('category', 'unknown') != 'unknown' for p in rows),
                'season_stat_known_n': {k: sum(p.get(k) is not None for p in rows) for k in STATS},
                'registered_roster_complete': complete,
                'qualification': 'counts of observed facts, not verified absence of missing players'},
            'lineup_status': lineup_status, 'starting_n': len(starts), 'bench_n': len(bench),
            'confirmed_unavailable': [{'player_id': p['player_id'], 'name': p['name'], 'position': p.get('position'), 'reason': p['availability']} for p in absent],
            'doubtful_n': sum(p.get('availability') == 'doubtful' for p in rows),
            'unknown_availability_n': sum(p.get('availability', 'unknown') == 'unknown' for p in rows),
            'features': {'season_minutes_total': minutes, 'season_yellow_total': yellow, 'season_red_total': red,
                'observed_season_minutes_sum': observed_total('season_minutes'),
                'observed_season_yellow_sum': observed_total('season_yellow'),
                'observed_season_red_sum': observed_total('season_red'),
                'yellow_per_player_90': 90 * yellow / minutes if minutes and yellow is not None else None,
                'red_per_player_90': 90 * red / minutes if minutes and red is not None else None,
                'unavailable_season_minute_share': share('season_minutes'),
                'unavailable_season_goal_share': share('season_goals'),
                'unavailable_season_assist_share': share('season_assists'),
                'unavailable_goalkeepers_observed': sum(p.get('position') == 'GK' for p in absent),
                'foreign_starters_exact': fstarts if all_starts_reg else None,
                'foreign_quota_starters_exact': sum(players[x]['registration']['counts_toward_foreign_quota'] for x in starts) if quota_known else None,
                'foreign_anticipated_minutes': anticipated},
            'cards_counting_convention': team.get('cards_counting_convention', 'unknown'),
            'qualification': 'current roster totals, not automatically full season team totals; player90 denominator; no nationality inference'}


def build_observations(payload, cutoff):
    block = payload.get('team_observations')
    if block is None:
        return {'schema': SCHEMA, 'status': 'missing', 'probability_effect_enabled': False}
    if not isinstance(block, dict) or block.get('schema') != SCHEMA or block.get('project') != 'jingcai':
        raise ValueError('须为竞彩来源情报包')
    check_match(block.get('match'), payload)
    if aware_datetime(payload['kickoff_at'], 'kickoff_at') <= cutoff:
        raise ValueError('情报快照必须在开球前')
    _future(block, cutoff)
    packets = block.get('packets')
    if not isinstance(packets, list):
        raise ValueError('packets须为列表')
    results = []; seen = set()
    for packet in packets:
        if not isinstance(packet, dict):
            raise ValueError('情报包须为对象')
        source = packet.get('source')
        if source not in SOURCE_HOSTS:
            raise ValueError('未登记情报来源')
        if source == 'fotmob_authorized' and packet.get('authorized_systematic_access') is not True:
            raise ValueError('FotMob定时接入缺授权声明')
        check_match(packet.get('match'), payload)
        if packet['match'] != block['match']:
            raise ValueError('来源包比赛绑定不一致')
        evidence = checked_evidence(packet.get('evidence'), cutoff)
        for e in evidence.values():
            host = urlsplit(e['url']).hostname
            if e['source'] != source or not any(host == h or host.endswith('.' + h) for h in SOURCE_HOSTS[source]):
                raise ValueError('来源名与证据域名不一致')
            if any(k.lower().replace('_', '').replace('-', '') in ('apikey', 'apitoken', 'authkey', 'token', 'authorization') for k, _ in parse_qsl(urlsplit(e['url']).query)):
                raise ValueError('证据URL不能含密钥')
        binding = packet.get('binding')
        if not isinstance(binding, dict):
            raise ValueError('缺来源原生身份映射')
        for k in ('event_id', 'home_id', 'away_id'):
            text(binding.get(k), 'binding.' + k)
        if binding['home_id'] == binding['away_id']:
            raise ValueError('主客原生球队ID相同')
        if binding.get('evidence_id') not in evidence:
            raise ValueError('比赛映射缺证据')
        status = packet.get('status')
        if status not in ('ok', 'missing', 'blocked', 'unsupported'):
            raise ValueError('来源状态无效')
        key = (source, binding['event_id'], digest(packet))
        if key in seen:
            raise ValueError('重复来源包')
        seen.add(key)
        teams = packet.get('teams')
        if not isinstance(teams, dict) or set(teams) != {'home', 'away'}:
            raise ValueError('来源须声明主客双方，未知填null')
        if source == 'api_football' and status == 'ok' and any(t is not None for t in teams.values()):
            context = packet.get('fixture_context')
            if not isinstance(context, dict) or context.get('schema') != 'api-football-prematch-fixture-v1' or context.get('status') != 'NS':
                raise ValueError('API-Football情报缺核验的赛前赛程上下文')
            for k in ('event_id', 'home_id', 'away_id', 'competition_id', 'season_id'):
                if not isinstance(context.get(k), str) or context[k] != binding.get(k):
                    raise ValueError('API-Football赛程上下文ID不符')
            if aware_datetime(context.get('kickoff_at'), 'fixture_context.kickoff_at') != aware_datetime(payload['kickoff_at'], 'kickoff_at') or context.get('evidence_id') not in evidence:
                raise ValueError('API-Football赛程上下文时间/证据不符')
        parsed = {s: _team(teams[s], s, packet, evidence, payload) if teams[s] is not None else None for s in ('home', 'away')}
        if status != 'ok' and any(t and t['observed_players_n'] for t in parsed.values()):
            raise ValueError('失败来源不能含可用球员数据')
        ages = {key: {'collected_age_seconds': (cutoff - aware_datetime(e['collected_at'], 'collected_at')).total_seconds(),
                      'available_age_seconds': (cutoff - aware_datetime(e['available_at'], 'available_at')).total_seconds(),
                      'published_age_seconds': (cutoff - aware_datetime(e['published_at'], 'published_at')).total_seconds() if e.get('published_at') else None}
                for key, e in evidence.items()}
        freshness = {'oldest_collection_age_seconds': max(a['collected_age_seconds'] for a in ages.values()),
                     'newest_collection_age_seconds': min(a['collected_age_seconds'] for a in ages.values()),
                     'relative_to': 'snapshot_at', 'qualification': 'collection age does not prove source content update time'}
        results.append({'source': source, 'priority': PRIORITY[source], 'status': status, 'reason': packet.get('reason'),
                        'event_id': binding['event_id'], 'teams': parsed, 'packet_sha256': digest(packet),
                        'evidence_ages': ages, 'freshness': freshness,
                        'available_at': max(aware_datetime(e['available_at'], 'available_at') for e in evidence.values()).isoformat(),
                        'evidence_sha256': {k: e['sha256'] for k, e in evidence.items()}})
    # 不同来源分别输出，不按姓名合并缺阵与贡献率。相同来源同一球队的不同包也不静默覆盖。
    selected = {}; dimensions = {}; conflicts = []
    for side in ('home', 'away'):
        candidates = [r for r in results if r['status'] == 'ok' and r['teams'][side] and r['teams'][side]['observed_players_n']]
        by_source = {}
        for r in candidates:
            by_source.setdefault(r['source'], []).append(r)
        valid = []
        for source, rs in by_source.items():
            if len(rs) > 1:
                conflicts.append({'side': side, 'source': source, 'reason': 'multiple_packets_require_resolution'})
            else:
                valid.extend(rs)
        chosen = min(valid, key=lambda r: (r['priority'], r['source'])) if valid else None
        selected[side] = {'source': chosen['source'], 'packet_sha256': chosen['packet_sha256'], 'freshness': deepcopy(chosen['freshness']), **deepcopy(chosen['teams'][side])} if chosen else None
        # 按信息种类分别选来源。官方伤停不覆盖另一来源确认的首发；不把两者球员名单联结。
        eligible = {
            'availability': lambda t: any(p.get('availability', 'unknown') != 'unknown' for p in t['observed_players']),
            'lineup': lambda t: t['lineup_status'] in ('confirmed', 'expected'),
            'discipline': lambda t: any(p.get('season_yellow') is not None or p.get('season_red') is not None for p in t['observed_players']),
            'registration': lambda t: any((p.get('registration') or {}).get('category', 'unknown') != 'unknown' for p in t['observed_players']),
        }
        dimensions[side] = {}
        for kind, predicate in eligible.items():
            usable = [r for r in valid if predicate(r['teams'][side])]
            pick = min(usable, key=lambda r: (r['priority'], r['source'])) if usable else None
            dimensions[side][kind] = {'source': pick['source'], 'packet_sha256': pick['packet_sha256'], 'freshness': deepcopy(pick['freshness']), **deepcopy(pick['teams'][side])} if pick else None
    ready = sum(v is not None for v in selected.values())
    return {'schema': SCHEMA, 'status': 'ok' if ready == 2 else 'partial' if ready else 'missing',
            'match': deepcopy(block['match']), 'sources': results, 'selected_by_side': selected,
            'selected_by_dimension': dimensions,
            'conflicts': conflicts, 'input_sha256': digest(block), 'probability_effect_enabled': False,
            'provenance_status': 'declared_binding_and_evidence; bytes_verified_by_importer_only',
            'feature_policy': 'official_sporttery_first; no_cross_namespace_player_merge; null_is_unknown',
            'production_gate': 'probability coefficients require separately validated temporal training; not enabled'}


def merge_same_source(packets):
    """显式合并同源同fixture同ID信息；已知字段冲突一律拒绝，不按姓名联结。"""
    grouped = {}; output = []
    for original in packets:
        packet = deepcopy(original)
        if packet.get('status') != 'ok':
            output.append(packet); continue
        binding = packet['binding']
        key = (packet['source'], binding['event_id'], digest(packet['match']))
        grouped.setdefault(key, []).append(packet)
    for group in grouped.values():
        if len(group) == 1:
            output.extend(group); continue
        base = None; digests = []
        for i, packet in enumerate(group):
            digests.append(digest(packet))
            names = {e['id']: f'{i}:{e["id"]}' for e in packet['evidence']}
            if len(names) != len(packet['evidence']):
                raise ValueError('同源合并存在重复证据ID')
            def rename(value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        if (k == 'evidence_id' or k.endswith('_evidence_id')) and v is not None:
                            if v not in names: raise ValueError('合并引用未知证据')
                            value[k] = names[v]
                        else: rename(v)
                elif isinstance(value, list):
                    for v in value: rename(v)
            rename(packet)
            for e in packet['evidence']: e['id'] = names[e['id']]
            if base is None:
                base = packet; continue
            for k in ('home_id', 'away_id', 'competition_id', 'season_id'):
                if base['binding'].get(k) != packet['binding'].get(k):
                    raise ValueError('同源合并原生身份/赛季不一致')
            base['evidence'].extend(packet['evidence'])
            for side in ('home', 'away'):
                incoming = packet['teams'][side]; current = base['teams'][side]
                if incoming is None: continue
                if current is None:
                    base['teams'][side] = incoming; continue
                for k in ('team_id', 'player_id_namespace', 'roster_complete'):
                    if current[k] != incoming[k]: raise ValueError('同源名单作用域冲突')
                if current['roster_complete'] and {p['player_id'] for p in current['players']} != {p['player_id'] for p in incoming['players']}:
                    raise ValueError('同源完整名单不一致')
                if current.get('lineup') and incoming.get('lineup'):
                    a = {k: v for k, v in current['lineup'].items() if k != 'evidence_id'}
                    b = {k: v for k, v in incoming['lineup'].items() if k != 'evidence_id'}
                    if a != b: raise ValueError('同源阵容冲突')
                elif incoming.get('lineup'): current['lineup'] = incoming['lineup']
                by_id = {p['player_id']: p for p in current['players']}
                if len(by_id) != len(current['players']): raise ValueError('合并前重复球员ID')
                incoming_ids = [p['player_id'] for p in incoming['players']]
                if len(set(incoming_ids)) != len(incoming_ids): raise ValueError('合并前重复球员ID')
                for player in incoming['players']:
                    pid = player['player_id']
                    if pid not in by_id:
                        current['players'].append(player); by_id[pid] = player; continue
                    old = by_id[pid]
                    for field in ('availability', 'position'):
                        a, b = old.get(field, 'unknown'), player.get(field, 'unknown')
                        if a != 'unknown' and b != 'unknown' and a != b: raise ValueError('同源球员状态冲突')
                        if a == 'unknown' and b != 'unknown':
                            old[field] = b
                            if field == 'availability': old['availability_evidence_id'] = player['availability_evidence_id']
                    for field in STATS + ('anticipated_minutes',):
                        a, b = old.get(field), player.get(field)
                        if a is not None and b is not None and a != b: raise ValueError('同源球员统计冲突')
                        if a is None and b is not None:
                            old[field] = b
                            proof = 'anticipated_minutes_evidence_id' if field == 'anticipated_minutes' else 'season_stats_evidence_id'
                            old[proof] = player[proof]
                            if field != 'anticipated_minutes': old['season_stats_scope'] = player['season_stats_scope']
                    a, b = old.get('registration') or {}, player.get('registration') or {}
                    known_a = a.get('category', 'unknown') != 'unknown' or a.get('counts_toward_foreign_quota') is not None
                    known_b = b.get('category', 'unknown') != 'unknown' or b.get('counts_toward_foreign_quota') is not None
                    if known_a and known_b and {k: v for k, v in a.items() if k != 'evidence_id'} != {k: v for k, v in b.items() if k != 'evidence_id'}:
                        raise ValueError('同源外援注册冲突')
                    if not known_a and known_b: old['registration'] = b
                a, b = current.get('cards_counting_convention'), incoming.get('cards_counting_convention')
                if a and b and a != b: raise ValueError('同源红黄牌统计口径冲突')
                if b: current['cards_counting_convention'] = b
        base['merged_packet_sha256'] = digests
        output.append(base)
    return output
