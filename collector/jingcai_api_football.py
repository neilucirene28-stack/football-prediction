"""竞彩API-Football赛前赛程和完整分页；每页有独立原件证据。"""
from copy import deepcopy
from pathlib import Path
import json

from engine.jingcai_inputs import aware_datetime
from . import jingcai_intelligence as source

CONTEXT_SCHEMA = 'api-football-prematch-fixture-v1'
ROOT = 'https://v3.football.api-sports.io/'


def check_binding(binding):
    for key in ('event_id', 'home_id', 'away_id', 'competition_id', 'season_id'):
        value = binding.get(key)
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise ValueError('API-Football缺数字原生ID:' + key)
    if binding['home_id'] == binding['away_id']:
        raise ValueError('API-Football主客队ID相同')


def fixture_context(data, binding, match, *, evidence_id='fixture'):
    check_binding(binding)
    source.check_match(match, match)
    if not isinstance(data, dict) or data.get('errors') or str((data.get('parameters') or {}).get('id')) != binding['event_id']:
        raise ValueError('API-Football赛程业务错误或查询ID不符')
    rows = data.get('response')
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('API-Football目标赛程不唯一或缺失')
    row = rows[0]; f = row.get('fixture') or {}; league = row.get('league') or {}
    if str(f.get('id')) != binding['event_id']:
        raise ValueError('API-Football赛程ID不匹配')
    if str(league.get('id')) != binding['competition_id'] or str(league.get('season')) != binding['season_id']:
        raise ValueError('API-Football赛程赛事/赛季ID不匹配')
    if aware_datetime(f.get('date'), 'API.fixture.date') != aware_datetime(match['kickoff_at'], 'kickoff_at'):
        raise ValueError('API-Football赛程开球时间不匹配')
    if f.get('timestamp') is not None:
        if isinstance(f['timestamp'], bool) or not isinstance(f['timestamp'], int) or f['timestamp'] != aware_datetime(f['date'], 'API.fixture.date').timestamp():
            raise ValueError('API-Football赛程时间戳与日期矛盾')
    for side in ('home', 'away'):
        if str(((row.get('teams') or {}).get(side) or {}).get('id')) != binding[side + '_id']:
            raise ValueError('API-Football赛程主客队ID不匹配')
    if (f.get('status') or {}).get('short') != 'NS':
        raise ValueError('API-Football赛程不是未开赛NS状态')
    return {'schema': CONTEXT_SCHEMA, **{k: binding[k] for k in ('event_id', 'home_id', 'away_id', 'competition_id', 'season_id')},
            'kickoff_at': f['date'], 'status': 'NS', 'evidence_id': evidence_id}


def _usable_meta(meta):
    if meta.get('source') != 'api_football' or meta.get('status') != 'ok' or meta.get('http_status') != 200 or meta.get('truncated'):
        raise ValueError('API-Football失败原件不能生成有效情报')


def _paging(data):
    page = data.get('paging')
    if not isinstance(page, dict): raise ValueError('API-Football缺分页声明')
    for key in ('current', 'total'):
        if isinstance(page.get(key), bool) or not isinstance(page.get(key), int) or page[key] < 1:
            raise ValueError('API-Football分页须为正整数')
    if page['current'] > page['total']: raise ValueError('API-Football页码超出总页数')
    return page['current'], page['total']


def _player_scope(data, binding, expected_team):
    params = data.get('parameters') or {}
    if str(params.get('team')) != expected_team or str(params.get('league')) != binding['competition_id'] or str(params.get('season')) != binding['season_id']:
        raise ValueError('API-Football不同球队/赛事/赛季的分页不能混合')
    for row in data['response']:
        stat = [s for s in row.get('statistics', []) if str((s.get('team') or {}).get('id')) == expected_team and
                str((s.get('league') or {}).get('id')) == binding['competition_id'] and
                str((s.get('league') or {}).get('season')) == binding['season_id']]
        if len(stat) != 1: raise ValueError('API-Football分页内球员统计作用域缺失或不唯一')


def bundle(fixture, fixture_meta, responses, metas, binding, match, kind):
    """全部响应通过后才产出；不把统计分页完整当成注册名单完整。"""
    _usable_meta(fixture_meta)
    context = fixture_context(fixture, binding, match)
    if kind not in ('lineups', 'injuries', 'players') or not responses or len(responses) != len(metas):
        raise ValueError('API-Football情报端点或原件数量无效')
    if kind != 'players' and len(responses) != 1:
        raise ValueError('非球员统计端点只允许单响应')
    expected_team = binding['away_id'] if binding.get('stats_team_side') == 'away' else binding['home_id']
    if binding.get('stats_team_side', 'home') not in ('home', 'away'):
        raise ValueError('统计球队侧无效')
    pages = {}; declared_total = None
    for data, meta in zip(responses, metas):
        _usable_meta(meta)
        if not isinstance(data, dict) or data.get('errors'):
            raise ValueError('API-Football情报返回业务错误')
        rows = data.get('response')
        if not isinstance(rows, list) or isinstance(data.get('results'), bool) or not isinstance(data.get('results'), int) or data['results'] != len(rows):
            raise ValueError('API-Football结果数量声明不匹配')
        if kind == 'players':
            current, total = _paging(data)
            if len(rows) > 20 or current < total and len(rows) != 20:
                raise ValueError('API-Football每页20条契约不符，疑似截断页')
            params = data.get('parameters') or {}
            _player_scope(data, binding, expected_team)
            if str(params.get('page', current)) != str(current):
                raise ValueError('API-Football查询页码与返回页码不同')
            if current in pages: raise ValueError('API-Football重复页码')
            if declared_total is not None and total != declared_total:
                raise ValueError('API-Football分页总数在采集期间变化')
            declared_total = total
        else:
            current = total = 1
        pages[current] = (data, meta)
    if set(pages) != set(range(1, (declared_total or 1) + 1)):
        raise ValueError('API-Football缺页，不产出部分统计')
    output = source._packet(fixture_meta, binding, match, {'home': None, 'away': None}, 'missing')
    output['binding']['evidence_id'] = 'fixture'
    output['evidence'] = [source.evidence(fixture_meta, 'fixture')]
    output['fixture_context'] = context
    output['api_kind'] = kind
    output['pagination'] = {'complete': True, 'pages': len(pages), 'expected_team_id': expected_team if kind == 'players' else None,
                            'qualification': 'complete query pages, not a complete current registration roster'}
    observed = {'home': set(), 'away': set()}
    for page, (data, meta) in sorted(pages.items()):
        # 已在本层验证全部分页；单页结构解析保持向后兼容但不能自行宣称完整。
        single = deepcopy(data)
        if kind == 'players': single['paging'] = {'current': 1, 'total': 1}
        parsed = source.api_football(single, meta, binding, match, kind)
        if kind == 'players':
            parsed['teams']['home' if expected_team == binding['away_id'] else 'away'] = None
        eid = f'data-page-{page}'
        output['evidence'].append(source.evidence(meta, eid))
        for side in ('home', 'away'):
            team = parsed['teams'][side]
            if team is None: continue
            for player in team['players']:
                if player['player_id'] in observed[side]: raise ValueError('API-Football跨页重复球员ID')
                observed[side].add(player['player_id'])
                for field in ('availability_evidence_id', 'season_stats_evidence_id'):
                    if player.get(field): player[field] = eid
            if team.get('lineup'): team['lineup']['evidence_id'] = eid
            if output['teams'][side] is None:
                output['teams'][side] = team
            else:
                output['teams'][side]['players'].extend(team['players'])
    output['status'] = 'ok' if any(output['teams'].values()) else 'missing'
    if output['status'] == 'missing': output['reason'] = 'source_did_not_publish_target_information'
    return output


def collect(binding, match, directory, *, kind, key, max_pages=10):
    """先核赛程，按页串行采集；失败/缺页停止，保留失败前原件，不发布部分玩家统计。"""
    if not key: raise ValueError('缺API_FOOTBALL_KEY，未发起请求')
    check_binding(binding)
    source.check_match(match, match)
    if kind not in ('lineups', 'injuries', 'players'): raise ValueError('API情报类型无效')
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
        raise ValueError('max_pages须为1至100整数')
    if aware_datetime(match['kickoff_at'], 'kickoff_at') <= source.datetime.now(source.timezone.utc):
        raise ValueError('已开球，不能采集新的赛前情报')
    root = Path(directory); root.mkdir(parents=True, exist_ok=False)
    saved_evidence = []
    def request(endpoint, params, folder, eid):
        raw, meta = source.fetch(ROOT + endpoint + '?' + source.urlencode(params), 'api_football', root / folder,
                                 headers={'x-apisports-key': key})
        saved_evidence.append(source.evidence(meta, eid))
        return raw, meta
    def failure(meta, reason, status=None):
        p = source._packet(meta, binding, match, {'home': None, 'away': None}, status or meta['status'], reason)
        p['evidence'] = deepcopy(saved_evidence); p['binding']['evidence_id'] = saved_evidence[0]['id']
        p['api_kind'] = kind; p['pagination'] = {'complete': False, 'qualification': 'no partial statistics published'}
        return p
    raw, fm = request('fixtures', {'id': binding['event_id']}, 'fixture', 'fixture')
    if fm['status'] != 'ok': return failure(fm, 'fixture_http_failed')
    try:
        fd = json.loads(raw); fixture_context(fd, binding, match)
    except (ValueError, TypeError, KeyError): return failure(fm, 'fixture_response_failed_validation', 'unsupported')
    responses = []; metas = []; total = 1; page = 1
    while page <= total:
        if aware_datetime(match['kickoff_at'], 'kickoff_at') <= source.datetime.now(source.timezone.utc):
            return failure(metas[-1] if metas else fm, 'kickoff_passed_during_collection', 'unsupported')
        latest_meta = metas[-1] if metas else fm
        if any(v == 0 for v in (latest_meta.get('quota') or {}).values()):
            return failure(latest_meta, 'provider_quota_exhausted', 'unsupported')
        if kind == 'players':
            side = binding.get('stats_team_side', 'home')
            if side not in ('home', 'away'): raise ValueError('统计球队侧无效')
            params = {'team': binding[side + '_id'], 'league': binding['competition_id'], 'season': binding['season_id'], 'page': page}
        else: params = {'fixture': binding['event_id']}
        endpoint = 'fixtures/lineups' if kind == 'lineups' else kind
        raw, meta = request(endpoint, params, f'data-page-{page}', f'data-page-{page}')
        if meta['status'] != 'ok': return failure(meta, 'data_page_http_failed')
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('errors'): raise ValueError('业务错误')
            if kind == 'players':
                current, declared = _paging(data)
                if current != page or page > 1 and declared != total: raise ValueError('页码/总页数变化')
                _player_scope(data, binding, binding[side + '_id'])
                if declared > max_pages: return failure(meta, 'pagination_exceeds_request_budget', 'unsupported')
                total = declared
            responses.append(data); metas.append(meta)
            # 每收到一页就核查作用域，不为错误绑定继续消耗额度。
            one = deepcopy(data)
            if kind == 'players': one['paging'] = {'current': 1, 'total': 1}
            source.api_football(one, meta, binding, match, kind)
        except (ValueError, TypeError, KeyError): return failure(meta, 'data_page_failed_validation', 'unsupported')
        page += 1
    try: return bundle(fd, fm, responses, metas, binding, match, kind)
    except (ValueError, TypeError, KeyError): return failure(metas[-1], 'bundle_failed_validation', 'unsupported')
