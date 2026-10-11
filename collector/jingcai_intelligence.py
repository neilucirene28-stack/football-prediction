"""竞彩专用来源适配器：保留原件、原生ID，未绑定姓名仅作待核验记录。"""
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler

from engine.jingcai_inputs import aware_datetime
from engine.jingcai_observations import SCHEMA, SOURCE_HOSTS, check_match


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 密钥请求不经重定向发送到另一个地址。


def fetch(url, source, directory, *, headers=None, timeout=20):
    """单次请求，无安全拦截绕过、无自动重试。返回HTTP原件和实际可得时间。"""
    parsed_url = urlsplit(url)
    host = parsed_url.hostname or ''
    secret_query = any(k.lower().replace('_', '').replace('-', '') in ('apikey', 'apitoken', 'authkey', 'token', 'authorization') for k, _ in parse_qsl(parsed_url.query))
    if parsed_url.scheme != 'https' or parsed_url.username or parsed_url.password or secret_query:
        raise ValueError('采集URL须为无凭据HTTPS地址，密钥只能经请求头传入')
    if source not in SOURCE_HOSTS or not any(host == h or host.endswith('.' + h) for h in SOURCE_HOSTS[source]):
        raise ValueError('来源域名不匹配')
    if source == 'fotmob_authorized':
        raise ValueError('此采集器不提供FotMob自动请求；只支持已授权原件导入')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    meta = {'source': source, 'url': url, 'request_started_at': datetime.now(timezone.utc).isoformat()}
    raw = b''
    try:
        try:
            response = build_opener(NoRedirect()).open(Request(url, headers={'User-Agent': 'JingcaiIntelligence/1.0', **(headers or {})}), timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            # 重定向已禁用，额外核对返回URL。
            target = urlsplit(response.url).hostname or ''
            if not any(target == h or target.endswith('.' + h) for h in SOURCE_HOSTS[source]):
                raise ValueError('来源跳转到未登记域名')
            raw = response.read(8 * 1024 * 1024 + 1)
            meta.update(http_status=response.code, bytes=len(raw), truncated=len(raw) > 8 * 1024 * 1024,
                        content_type=response.headers.get('Content-Type'))
            if source == 'api_football':
                meta['quota'] = {}
                for key, header in (('daily_remaining', 'x-ratelimit-requests-remaining'), ('minute_remaining', 'X-RateLimit-Remaining')):
                    value = response.headers.get(header)
                    meta['quota'][key] = int(value) if isinstance(value, str) and value.isascii() and value.isdigit() else None
    except (URLError, TimeoutError, OSError) as exc:
        meta['error_type'] = type(exc).__name__
    meta['collected_at'] = datetime.now(timezone.utc).isoformat()
    meta['available_at'] = meta['collected_at']
    meta['sha256'] = hashlib.sha256(raw).hexdigest()
    meta['status'] = 'ok' if meta.get('http_status') == 200 and not meta.get('truncated') else 'blocked' if meta.get('http_status') in (401, 403, 429, 567) else 'missing'
    (directory / 'response.body').write_bytes(raw)
    (directory / 'response.meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return raw, meta


def evidence(meta, eid='http'):
    return {k: meta[k] for k in ('source', 'url', 'sha256', 'collected_at', 'available_at')} | {'id': eid}


def _player(pid, name, pos='unknown'):
    return {'player_id': str(pid), 'name': name, 'position': pos, 'availability': 'unknown',
            'season_minutes': None, 'season_goals': None, 'season_assists': None,
            'season_yellow': None, 'season_red': None, 'registration': {'category': 'unknown', 'counts_toward_foreign_quota': None}}


def _packet(meta, binding, match, teams, status='ok', reason=None):
    check_match(match, match)
    return {'source': meta['source'], 'match': dict(match), 'binding': {**binding, 'evidence_id': 'http'},
            'status': status, 'reason': reason, 'evidence': [evidence(meta)], 'teams': teams}


def espn_summary(data, meta, binding, match):
    c = (data.get('header', {}).get('competitions') or [{}])[0]
    if str(c.get('id')) != binding['event_id']:
        raise ValueError('ESPN event ID与显式映射不匹配（禁止使用澳客mid）')
    if aware_datetime(c.get('date'), 'ESPN.date') != aware_datetime(match['kickoff_at'], 'kickoff_at'):
        raise ValueError('ESPN开球时间不匹配')
    competitors = c.get('competitors') or []
    for side in ('home', 'away'):
        items = [x for x in competitors if x.get('homeAway') == side]
        if len(items) != 1 or str(items[0].get('team', {}).get('id')) != binding[side + '_id']:
            raise ValueError('ESPN主客球队原生ID不匹配')
    if c.get('status', {}).get('type', {}).get('state') != 'pre':
        return _packet(meta, binding, match, {'home': None, 'away': None}, 'unsupported', 'live_or_postmatch_not_prematch')
    teams = {'home': None, 'away': None}
    for side in teams:
        rosters = [r for r in data.get('rosters', []) if str(r.get('team', {}).get('id')) == binding[side + '_id']]
        if len(rosters) > 1:
            raise ValueError('ESPN重复球队名单')
        if not rosters or not rosters[0].get('roster'):
            continue
        players = []; starters = []; bench = []
        for row in rosters[0]['roster']:
            athlete = row.get('athlete') or {}
            pid = athlete.get('id')
            if not pid:
                raise ValueError('ESPN球员缺原生ID，不按姓名猜测')
            pos = (row.get('position') or {}).get('abbreviation')
            players.append(_player(pid, athlete.get('displayName'), {'G': 'GK', 'GK': 'GK', 'D': 'DEF', 'M': 'MID', 'F': 'FWD'}.get(pos, 'unknown')))
            if isinstance(row.get('starter'), bool):
                (starters if row['starter'] else bench).append(str(pid))
        # starter布尔是源声明，仅在pre且恰好11人时提升confirmed；不读取赛后stats。
        lineup = {'status': 'confirmed', 'starting_ids': starters, 'bench_ids': bench, 'evidence_id': 'http'} if len(starters) == 11 and len(starters) + len(bench) == len(players) else None
        teams[side] = {'team_id': binding[side + '_id'], 'player_id_namespace': 'espn', 'roster_complete': False,
                       'players': players, 'lineup': lineup}
    return _packet(meta, binding, match, teams, 'ok' if any(teams.values()) else 'missing', 'source_roster_not_published' if not any(teams.values()) else None)


def api_football(data, meta, binding, match, kind):
    """处理单端点原件；列表返回并不证明完整注册名单。"""
    if data.get('errors'):
        raise ValueError('API-Football返回业务错误')
    params = data.get('parameters') or {}
    if kind in ('lineups', 'injuries') and str(params.get('fixture')) != binding['event_id']:
        raise ValueError('API-Football返回的查询fixture不匹配')
    if kind == 'players' and (str(params.get('league')) != binding.get('competition_id') or str(params.get('season')) != binding.get('season_id') or str(params.get('team')) not in (binding['home_id'], binding['away_id'])):
        raise ValueError('API-Football返回的统计查询作用域不匹配')
    rows = data.get('response')
    if not isinstance(rows, list):
        raise ValueError('API-Football结构无效')
    if kind == 'players' and data.get('paging', {}).get('total', 1) != 1:
        raise ValueError('球员统计有多页，须全部归档并完成分页后导入')
    teams = {'home': None, 'away': None}
    for side in teams:
        tid = binding[side + '_id']; players = []; lineup = None
        if kind == 'lineups':
            selected = [r for r in rows if str((r.get('team') or {}).get('id')) == tid]
            if len(selected) > 1:
                raise ValueError('API-Football重复球队阵容')
            for r in selected:
                starts = []; bench = []
                for group, ids in (('startXI', starts), ('substitutes', bench)):
                    for entry in r.get(group, []):
                        p = entry.get('player') or {}
                        if not p.get('id'):
                            raise ValueError('阵容缺球员ID')
                        players.append(_player(p['id'], p.get('name'), {'G': 'GK', 'D': 'DEF', 'M': 'MID', 'F': 'FWD'}.get(p.get('pos'), 'unknown')))
                        ids.append(str(p['id']))
                if len(starts) == 11:
                    lineup = {'status': 'confirmed', 'starting_ids': starts, 'bench_ids': bench, 'evidence_id': 'http'}
        elif kind == 'injuries':
            for r in rows:
                if str(r.get('team', {}).get('id')) != tid:
                    continue
                if str(r.get('fixture', {}).get('id')) != binding['event_id']:
                    raise ValueError('伤停并非目标fixture')
                p = r.get('player') or {}
                if not p.get('id'):
                    raise ValueError('伤停缺球员ID')
                player = _player(p['id'], p.get('name'))
                raw_type = str(p.get('type') or ''); reason = str(p.get('reason') or '')
                status = 'doubtful' if 'Questionable' in raw_type else 'suspended' if 'suspend' in reason.lower() else 'injured' if raw_type == 'Missing Fixture' else 'unknown'
                player.update(availability=status, availability_evidence_id='http', raw_type=raw_type, raw_reason=reason)
                players.append(player)
        elif kind == 'players':
            for r in rows:
                stat = [s for s in r.get('statistics', []) if str(s.get('team', {}).get('id')) == tid and str(s.get('league', {}).get('id')) == binding.get('competition_id') and str(s.get('league', {}).get('season')) == binding.get('season_id')]
                if len(stat) > 1:
                    raise ValueError('重复球员统计作用域')
                if not stat:
                    continue
                p = r.get('player') or {}; s = stat[0]
                if not p.get('id'):
                    raise ValueError('统计缺球员ID')
                player = _player(p['id'], p.get('name'))
                player['nationality'] = p.get('nationality')  # 不推断注册类别。
                for key, group, field in (('season_minutes', 'games', 'minutes'), ('season_goals', 'goals', 'total'), ('season_assists', 'goals', 'assists'), ('season_yellow', 'cards', 'yellow'), ('season_red', 'cards', 'red')):
                    player[key] = (s.get(group) or {}).get(field)
                player.update(season_stats_evidence_id='http', season_stats_scope={k: match[k] for k in ('competition', 'season')})
                players.append(player)
        else:
            raise ValueError('未知API-Football情报类型')
        if players:
            teams[side] = {'team_id': tid, 'player_id_namespace': 'api_football', 'roster_complete': False, 'players': players, 'lineup': lineup,
                           'cards_counting_convention': 'provider_raw_red; yellowred_not_merged'}
    return _packet(meta, binding, match, teams, 'ok' if any(teams.values()) else 'missing')


def fotmob_roster(data):
    """离线名单修复，输出待绑定球员；不提供自动网络请求。"""
    groups = (data.get('squad') or {}).get('squad')
    if groups is None:
        return []
    if not isinstance(groups, list):
        raise ValueError('FotMob名单结构未知')
    out = []
    for group in groups:
        if group.get('title') == 'coach':
            continue
        if 'members' not in group:
            raise ValueError('FotMob名单须展开members')
        for p in group['members']:
            if not p.get('id'):
                raise ValueError('FotMob球员缺ID')
            out.append(_player(p['id'], p.get('name'), {'keepers': 'GK', 'defenders': 'DEF', 'midfielders': 'MID', 'attackers': 'FWD'}.get(group.get('title'), 'unknown')))
    return out


def availability(reason, condition, raw_status):
    """PI状态标签保持不确定，百分比不是已校准概率。"""
    if str(reason).strip().lower() == 'suspended':
        return 'suspended'
    if str(raw_status).strip() == 'Ruled Out':
        return 'injured'
    if str(condition).strip() == 'Passed Fit' or str(raw_status).strip() == '100%':
        return 'available'
    if str(raw_status).strip() in ('25%', '50%', '75%') or str(condition).strip() in ('Late Fitness Test', 'Currently Being Assessed'):
        return 'doubtful'
    return 'unknown'


def premierinjuries_table(page, meta, binding, match, player_bindings):
    """独立离线HTML解析，只读原始状态，不采用旧解析器推测的年份/缺阵标签。"""
    tables = re.findall(r'<table\b[^>]*class=[\"\'][^\"\']*injury-table-full[^\"\']*[\"\'][^>]*>(.*?)</table>', page, re.S)
    if len(tables) != 1: raise ValueError('PI伤停表结构不唯一或缺失')
    def clean(value):
        value = re.sub(r'<div\b[^>]*class=[\"\']mob-title[\"\'][^>]*>.*?</div>', '', value, flags=re.S)
        value = re.sub(r'<a\b[^>]*href=[\"\']/newsroom/epl/players/[^\"\']*[\"\'][^>]*>.*?</a>', '', value, flags=re.S)
        return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', value)).split())
    rows = []; team = None
    for attrs, body in re.findall(r'<tr\b([^>]*)>(.*?)</tr>', tables[0], re.S):
        if 'heading' in attrs:
            found = re.search(r'<div\b[^>]*class=[\"\']injury-team[\"\'][^>]*>(.*?)</div>', body, re.S)
            team = clean(found.group(1)) if found else None; continue
        if 'player-row' not in attrs: continue
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', body, re.S)
        if team is None or len(cells) < 6: raise ValueError('PI球员行结构不完整')
        player, reason, detail, potential, condition, status = map(clean, cells[:6])
        tid = re.search(r'\bteam_(\d+)\b', attrs)
        pid = re.search(r'\bdata-id=[\"\'](\d+)[\"\']', cells[6]) if len(cells) > 6 else None
        rows.append({'team': team, 'player': player, 'injury': reason, 'detail': detail,
                     'expected_return': potential, 'condition': condition, 'status_raw': status,
                     'native_team_id': tid.group(1) if tid else None, 'native_player_id': pid.group(1) if pid else None})
    teams = {'home': None, 'away': None}; unbound = []
    for row in rows:
        candidates = [b for b in player_bindings if b.get('team_name') == row['team'] and b.get('player_name') == row['player']]
        # 页面自身明确的原生team/player ID可直接绑定，不用跨来源姓名推断。
        sides = [s for s in teams if binding[s + '_id'] == row['native_team_id']]
        if not candidates and len(sides) == 1 and row['native_player_id']:
            candidates = [{'side': sides[0], 'team_id': row['native_team_id'], 'player_id': row['native_player_id'],
                           'source': 'premierinjuries', 'mapping_evidence_sha256': meta['sha256']}]
        if len(candidates) != 1:
            unbound.append(row); continue
        b = candidates[0]; side = b.get('side')
        if side not in teams or b.get('team_id') != binding[side + '_id'] or b.get('source') != 'premierinjuries' or not b.get('player_id') or b.get('mapping_evidence_sha256') != meta['sha256']:
            raise ValueError('PI显式球员身份/原件映射无效')
        if row['native_team_id'] and b['team_id'] != row['native_team_id'] or row['native_player_id'] and b['player_id'] != row['native_player_id']:
            raise ValueError('PI映射与页面原生ID矛盾')
        p = _player(b['player_id'], row['player'], b.get('position', 'unknown'))
        p.update(availability=availability(row['injury'], row['condition'], row['status_raw']),
                 availability_evidence_id='http', raw_status=row['status_raw'], raw_condition=row['condition'],
                 raw_detail=row['detail'], raw_return=row['expected_return'])
        if teams[side] is None:
            teams[side] = {'team_id': b['team_id'], 'player_id_namespace': 'premierinjuries', 'roster_complete': False, 'players': [], 'lineup': None}
        teams[side]['players'].append(p)
    packet = _packet(meta, binding, match, teams, 'ok' if any(teams.values()) else 'missing')
    packet['unbound_rows'] = [{k: r[k] for k in ('team', 'player', 'status_raw', 'condition', 'detail', 'expected_return')} for r in unbound]
    return packet


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.depth = 0; self.rows = []; self.row = None; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth == 1: self.rows = []
        if self.depth == 1 and tag == 'tr': self.row = []
        if self.depth == 1 and tag in ('td', 'th') and self.row is not None: self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(''.join(self.cell).split())); self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row); self.row = None
        if tag == 'table':
            if self.depth == 1: self.tables.append(self.rows)
            self.depth = max(0, self.depth - 1)


def sporttery_table(page):
    """仅解析已核查的伤停表头，不把赛季首发次数当作本场XI。无ID时不自动绑定。"""
    parser = _Tables(); parser.feed(page)
    expected = ['球队', '球员', '位置', '状态', '首发', '替补', '进球', '助攻']
    found = [t for t in parser.tables if expected in t]
    if not found:
        return {'status': 'unsupported', 'reason': 'no_verified_injury_table_structure', 'rows': []}
    output = []
    for table in found:
        start = table.index(expected)
        for row in table[start + 1:]:
            if len(row) == 1 and '暂无伤停数据' in row[0]:
                continue
            if len(row) != len(expected):
                raise ValueError('竞彩官方伤停表行结构不匹配')
            x = dict(zip(expected, row))
            output.append({'raw_fields': x, 'team_name': x['球队'], 'player_name': x['球员'],
                           'availability': 'suspended' if x['状态'] == '停赛' else 'injured' if x['状态'] in ('确定缺阵', '缺阵') else 'doubtful' if x['状态'] in ('出战成疑', '伤疑') else 'unknown',
                           'identity_status': 'unbound_no_native_player_id', 'lineup_status': 'unknown',
                           'qualification': '首发/替补列口径未证实；不能生成本场阵容或未经作用域核查的赛季贡献'})
    return {'status': 'ok' if output else 'missing', 'rows': output}


def bind_sporttery_rows(rows, meta, binding, match, player_bindings):
    """显式球员映射须含源原文、原生ID及证据；未映射行保留，不按姓名自动批准。"""
    teams = {'home': None, 'away': None}; excluded = []
    for row in rows:
        candidates = [b for b in player_bindings if b.get('team_name') == row['team_name'] and b.get('player_name') == row['player_name']]
        if len(candidates) != 1:
            excluded.append(row); continue
        b = candidates[0]; side = b.get('side')
        if side not in teams or b.get('team_id') != binding[side + '_id'] or b.get('source') != 'sporttery':
            raise ValueError('竞彩网球员显式映射不匹配')
        if not b.get('player_id') or b.get('mapping_evidence_sha256') != meta['sha256']:
            raise ValueError('竞彩网球员映射缺原件证据')
        p = _player(b['player_id'], row['player_name'], b.get('position', 'unknown'))
        p.update(availability=row['availability'], availability_evidence_id='http', raw_fields=row['raw_fields'])
        if teams[side] is None:
            teams[side] = {'team_id': b['team_id'], 'player_id_namespace': 'sporttery', 'roster_complete': False, 'players': [], 'lineup': None}
        teams[side]['players'].append(p)
    packet = _packet(meta, binding, match, teams, 'ok' if any(teams.values()) else 'missing')
    packet['unbound_rows'] = excluded
    return packet


def sportmonks_fixture(data, meta, binding, match):
    d = data.get('data')
    if not isinstance(d, dict) or str(d.get('id')) != binding['event_id']:
        raise ValueError('Sportmonks fixture ID不匹配')
    if str(d.get('league_id')) != binding.get('competition_id') or str(d.get('season_id')) != binding.get('season_id'):
        raise ValueError('Sportmonks赛事/赛季ID不匹配')
    stamp = d.get('starting_at_timestamp')
    if isinstance(stamp, bool) or not isinstance(stamp, int) or datetime.fromtimestamp(stamp, timezone.utc) != aware_datetime(match['kickoff_at'], 'kickoff_at'):
        raise ValueError('Sportmonks开球时间不匹配')
    for side in ('home', 'away'):
        participants = [p for p in d.get('participants', []) if (p.get('meta') or {}).get('location') == side]
        if len(participants) != 1 or str(participants[0].get('id')) != binding[side + '_id']:
            raise ValueError('Sportmonks主客原生ID不匹配')
    if d.get('state_id') != 1:
        return _packet(meta, binding, match, {'home': None, 'away': None}, 'unsupported', 'not_scheduled_prematch')
    confirmed = [m for m in d.get('metadata', []) if m.get('type_id') == 572 and str(m.get('metadatable_id')) == binding['event_id']]
    flag = (confirmed[0].get('values') or {}).get('confirmed') if len(confirmed) == 1 else None
    teams = {'home': None, 'away': None}
    for side in teams:
        players = {}; starts = []; bench = []
        for r in d.get('lineups', []):
            if str(r.get('team_id')) != binding[side + '_id']:
                continue
            if str(r.get('fixture_id')) != binding['event_id'] or not r.get('player_id'):
                raise ValueError('Sportmonks阵容绑定不匹配')
            pid = str(r['player_id'])
            if pid in players:
                raise ValueError('Sportmonks重复球员')
            players[pid] = _player(pid, r.get('player_name'), {24: 'GK', 25: 'DEF', 26: 'MID', 27: 'FWD'}.get(r.get('position_id'), 'unknown'))
            if r.get('type_id') in (11, 12):
                (starts if r['type_id'] == 11 else bench).append(pid)
        lineup = None
        if isinstance(flag, bool) and len(starts) == 11:
            lineup = {'status': 'confirmed' if flag else 'expected', 'starting_ids': starts, 'bench_ids': bench, 'evidence_id': 'http'}
        for r in d.get('sidelined', []):
            if str(r.get('participant_id')) != binding[side + '_id']:
                continue
            if str(r.get('fixture_id')) != binding['event_id'] or not r.get('player_id'):
                raise ValueError('Sportmonks伤停绑定不匹配')
            pid = str(r['player_id']); athlete = r.get('player') or {}
            p = players.setdefault(pid, _player(pid, athlete.get('display_name') or athlete.get('name'), {24: 'GK', 25: 'DEF', 26: 'MID', 27: 'FWD'}.get(athlete.get('position_id'), 'unknown')))
            category = (r.get('sideline') or {}).get('category')
            p.update(availability='suspended' if category == 'suspension' else 'injured' if category == 'injury' else 'unknown', availability_evidence_id='http')
        if players:
            teams[side] = {'team_id': binding[side + '_id'], 'player_id_namespace': 'sportmonks', 'roster_complete': False,
                           'players': list(players.values()), 'lineup': lineup}
    return _packet(meta, binding, match, teams, 'ok' if any(teams.values()) else 'missing')


def collect(source, binding, match, directory, *, kind=None, key=None, player_bindings=None, max_api_pages=10):
    """已知原生身份的单次采集，不搜索或翻译球队名称。"""
    if source == 'espn':
        slug = binding.get('league_code', '')
        if not re.fullmatch(r'[a-z0-9.]+', slug) or not binding['event_id'].isdigit():
            raise ValueError('ESPN原生联赛/event格式无效')
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={binding['event_id']}"
        raw, meta = fetch(url, source, directory)
        parser = lambda data: espn_summary(data, meta, binding, match)
    elif source == 'sporttery':
        raw, meta = fetch('https://www.sporttery.cn/jc/index.html', source, directory)
        if meta['status'] == 'ok':
            parsed = sporttery_table(raw.decode('utf-8'))
            return bind_sporttery_rows(parsed['rows'], meta, binding, match, player_bindings or binding.get('player_bindings', [])) if parsed['status'] == 'ok' else _packet(meta, binding, match, {'home': None, 'away': None}, parsed['status'], parsed.get('reason'))
        parser = None
    elif source == 'premierinjuries':
        raw, meta = fetch('https://www.premierinjuries.com/injury-table.php', source, directory)
        if meta['status'] == 'ok':
            return premierinjuries_table(raw.decode('utf-8'), meta, binding, match, player_bindings or binding.get('player_bindings', []))
        parser = None
    elif source == 'api_football':
        from .jingcai_api_football import collect as collect_api
        return collect_api(binding, match, directory, kind=kind, key=key, max_pages=max_api_pages)
    else:
        raise ValueError('此来源使用留存原件导入，不宣称自动接口已接通')
    if meta['status'] != 'ok':
        return _packet(meta, binding, match, {'home': None, 'away': None}, meta['status'], 'http_fetch_failed')
    return parser(json.loads(raw))
