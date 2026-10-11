"""竞彩球队/外援阵容情报：证据绑定与研究特征，不调整λ或胜率。"""
import hashlib
import json
import re
from urllib.parse import urlsplit

from .jingcai_inputs import aware_datetime, finite_number

SCHEMA = 'jingcai-team-intelligence-v1'
POSITIONS = ('GK', 'DEF', 'MID', 'FWD')
FOREIGN = ('foreign', 'domestic', 'unknown')
AVAILABILITY = ('available', 'injured', 'suspended', 'doubtful', 'unknown')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
                                    separators=(',',':'),default=str).encode()).hexdigest()


def text(value, path):
    if not isinstance(value,str) or not value.strip(): raise ValueError(path+'须为非空文本')
    return value


def count(value,path):
    if isinstance(value,bool) or not isinstance(value,int) or value<0:
        raise ValueError(path+'须为非负整数')
    return value


def checked_evidence(evidence, cutoff):
    if not isinstance(evidence,list) or not evidence: raise ValueError('球队情报缺来源证据')
    lookup={}
    for e in evidence:
        if not isinstance(e,dict): raise ValueError('球队情报证据须为对象')
        key=text(e.get('id'),'evidence.id')
        if key in lookup: raise ValueError('重复球队情报证据ID')
        text(e.get('source'),'evidence.source')
        url=text(e.get('url'),'evidence.url'); parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('证据URL须为无凭据的HTTP地址')
        if not isinstance(e.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',e['sha256']):
            raise ValueError('球队情报证据缺字节SHA256')
        stamps={}
        for field in ('collected_at','available_at','published_at'):
            if field=='published_at' and e.get(field) is None: continue
            stamps[field]=aware_datetime(e.get(field),'evidence.'+field)
            if stamps[field]>cutoff: raise ValueError('球队情报证据晚于快照截止，存在未来泄漏')
        if stamps['collected_at']>stamps['available_at']:
            raise ValueError('球队情报可得时间不得早于实际采集时间')
        if stamps.get('published_at',stamps['collected_at'])>stamps['collected_at']:
            raise ValueError('球队情报发布时间晚于采集时间')
        lookup[key]=e
    return lookup


def _side(block, side, payload, evidence):
    if not isinstance(block,dict): raise ValueError('teams.'+side+'须为对象')
    if block.get('team')!=payload[side]: raise ValueError('球队情报球队与预测不匹配')
    namespace=text(block.get('player_id_namespace'),'player_id_namespace')
    if not isinstance(block.get('roster_complete'),bool): raise ValueError('须声明roster_complete')
    complete=block['roster_complete']; roster=block.get('players')
    if not isinstance(roster,list) or not roster: raise ValueError('球员名单须为非空列表')
    ref=block.get('reference')
    if not isinstance(ref,dict): raise ValueError('缺参考阵容窗口')
    ref_n=count(ref.get('match_count'),'reference.match_count')
    history=payload.get(side+'_recent') or []
    if ref_n!=len(history): raise ValueError('参考场数与原近期战绩不一致')
    if ref.get('history_sha256')!=digest(history): raise ValueError('参考阵容未绑定原近期战绩')
    def evidence_id(value,path):
        if not isinstance(value,str) or value not in evidence: raise ValueError(path+'引用未知证据')
        return value
    evidence_id(ref.get('evidence_id'),'reference.evidence_id')
    players={}
    for p in roster:
        if not isinstance(p,dict): raise ValueError('球员须为对象')
        pid=text(p.get('player_id'),'player_id')
        if pid in players: raise ValueError('重复球员ID')
        text(p.get('name'),'player.name')
        if p.get('position') not in POSITIONS: raise ValueError('球员位置无效')
        if p.get('foreign_status') not in FOREIGN: raise ValueError('外援状态须明确或unknown')
        if p['foreign_status']!='unknown': evidence_id(p.get('registration_evidence_id'),'registration')
        if p.get('availability') not in AVAILABILITY: raise ValueError('球员可用状态无效')
        if p['availability']!='unknown': evidence_id(p.get('availability_evidence_id'),'availability')
        if p.get('reference_minutes_per_match') is not None:
            if not ref_n: raise ValueError('零参考场次不能声明分钟均值')
            finite_number(p['reference_minutes_per_match'],'reference_minutes_per_match',minimum=0,maximum=90)
        for key in ('season_minutes','season_goals','season_assists'):
            if p.get(key) is not None:
                count(p[key],key);evidence_id(p.get('season_stats_evidence_id'),'season_stats')
        # 注册和赛季贡献必须明确绑定当前球队/赛事/赛季，转会前数据不能静默合并。
        if any(p.get(k) is not None for k in ('season_minutes','season_goals','season_assists')) or p['foreign_status']!='unknown':
            expected={k:block['_match'][k] for k in ('competition','season')}
            expected['team']=payload[side]
            if p.get('registration_scope')!=expected: raise ValueError('球员赛季/球队作用域不匹配')
        players[pid]=p
    if complete and len(players)<11: raise ValueError('完整名单不足11人')
    refs=[p.get('reference_minutes_per_match') for p in players.values()]
    if all(v is not None for v in refs) and sum(float(v) for v in refs)>990+1e-8:
        raise ValueError('参考阵容须为常规90分钟口径，总分钟超出990')
    lineup=block.get('lineup'); starting=[];bench=[]; anticipated=None;status='unknown';allocation_complete=False
    if lineup is not None:
        if not isinstance(lineup,dict) or lineup.get('status') not in ('expected','confirmed'):
            raise ValueError('阵容状态无效')
        status=lineup['status'];evidence_id(lineup.get('evidence_id'),'lineup')
        starting=lineup.get('starting_ids');bench=lineup.get('bench_ids')
        if not isinstance(starting,list) or not isinstance(bench,list): raise ValueError('首发/替补须为列表')
        if any(not isinstance(pid,str) or pid not in players for pid in starting+bench):
            raise ValueError('首发/替补球员ID不在本来源名单中')
        if len(set(starting+bench))!=len(starting+bench): raise ValueError('首发/替补名单重复或重叠')
        if len(starting)>11 or (status=='confirmed' and len(starting)!=11):
            raise ValueError('确认首发须有11人')
        if any(players[pid]['availability'] in ('injured','suspended') for pid in starting+bench):
            raise ValueError('阵容与确认缺阵情报冲突，须重新核验')
        anticipated=lineup.get('anticipated_minutes')
        allocation_complete=lineup.get('minute_allocation_complete',False)
        if not isinstance(allocation_complete,bool): raise ValueError('分钟分配完整度须为布尔值')
        if allocation_complete and (not complete or len(starting)!=11 or anticipated is None):
            raise ValueError('完整分钟分配须有完整名单、11人首发及分钟数')
        if anticipated is not None:
            if not isinstance(anticipated,dict) or not set(starting+bench)<=set(anticipated)<=set(players):
                raise ValueError('预计分钟须覆盖全部列出的首发/替补，不能缺项补零')
            if allocation_complete and set(anticipated)!=set(players):
                raise ValueError('完整分钟分配须覆盖全部注册名单，未使用球员显式填0')
            vals=[finite_number(v,'anticipated_minutes',minimum=0,maximum=90) for v in anticipated.values()]
            if sum(vals)>990+1e-8: raise ValueError('预计常规时间总分钟超出990')
            if any(float(v)>0 and pid not in starting+bench for pid,v in anticipated.items()):
                raise ValueError('名单外球员不能有预计出场分钟')
    unavailable=[p for p in players.values() if p['availability'] in ('injured','suspended')]
    def observed_sum(items,field):
        return sum(float(p[field]) for p in items) if all(p.get(field) is not None for p in items) else None
    foreign=[p for p in players.values() if p['foreign_status']=='foreign']
    unknown_foreign=sum(p['foreign_status']=='unknown' for p in players.values())
    unknown_availability=sum(p['availability'] in ('unknown','doubtful') for p in players.values())
    starter_foreign=sum(players[pid]['foreign_status']=='foreign' for pid in starting)
    starter_unknown=sum(players[pid]['foreign_status']=='unknown' for pid in starting)
    exact_starters=starter_foreign if len(starting)==11 and not starter_unknown else None
    ref_total=observed_sum(list(players.values()),'reference_minutes_per_match') if complete else None
    ref_foreign=observed_sum(foreign,'reference_minutes_per_match') if complete and not unknown_foreign else None
    foreign_expected=None
    if allocation_complete and not unknown_foreign:
        foreign_expected=sum(float(v) for pid,v in anticipated.items() if players[pid]['foreign_status']=='foreign')
    # 无完整分钟分配就不计算阵容变化，不能把确认首发直接视为踢满90分钟。
    exposure_delta=None
    if allocation_complete and ref_total is not None:
        exposure_delta=sum(float(v) for v in anticipated.values())-ref_total
    total_goals=observed_sum(list(players.values()),'season_goals') if complete else None
    total_assists=observed_sum(list(players.values()),'season_assists') if complete else None
    missing_goals=observed_sum(unavailable,'season_goals')
    missing_assists=observed_sum(unavailable,'season_assists')
    replacement=block.get('replacements',[])
    if not isinstance(replacement,list): raise ValueError('替代情况须为列表')
    seen=set()
    for pair in replacement:
        if not isinstance(pair,dict): raise ValueError('替代情况须为对象')
        out,inside=pair.get('absent_id'),pair.get('replacement_id')
        if not isinstance(out,str) or not isinstance(inside,str) or out not in players or inside not in players or out==inside or out in seen:
            raise ValueError('替代球员身份无效或重复')
        if players[out]['availability'] not in ('injured','suspended'):
            raise ValueError('替代记录必须对应确认缺阵')
        if players[inside]['availability']!='available': raise ValueError('替代球员可用性未确认')
        evidence_id(pair.get('evidence_id'),'replacement');seen.add(out)
    return {
        'team':payload[side],'player_id_namespace':namespace,'roster_complete':complete,
        'roster_n':len(players),'reference_match_count':ref_n,
        'reference_history_sha256':ref['history_sha256'],'lineup_status':status,
        'starting_n':len(starting),'bench_n':len(bench),'foreign_starters_exact':exact_starters,
        'minute_allocation_complete':allocation_complete,'unknown_or_doubtful_availability_n':unknown_availability,
        'foreign_starters_known':starter_foreign,'foreign_starters_unknown':starter_unknown,
        'observed_foreign_roster_n':len(foreign),'unknown_registration_n':unknown_foreign,
        'confirmed_unavailable':[{'player_id':p['player_id'],'name':p['name'],
                                 'position':p['position'],'foreign_status':p['foreign_status'],
                                 'reason':p['availability']} for p in unavailable],
        'replacement_pairs':[dict(p) for p in replacement],
        'features':{
            'foreign_reference_minutes_per_match':ref_foreign,
            'foreign_anticipated_minutes':foreign_expected,
            'foreign_minutes_delta':foreign_expected-ref_foreign if foreign_expected is not None and ref_foreign is not None else None,
            'unavailable_reference_minutes_observed':observed_sum(unavailable,'reference_minutes_per_match'),
            'unavailable_season_goal_share':missing_goals/total_goals if not unknown_availability and missing_goals is not None and total_goals else None,
            'unavailable_season_assist_share':missing_assists/total_assists if not unknown_availability and missing_assists is not None and total_assists else None,
            'unavailable_goalkeeper_count_observed':sum(p['position']=='GK' for p in unavailable),
            'lineup_total_minutes_delta':exposure_delta,
            'starting_reference_continuity':observed_sum([players[pid] for pid in starting],
                'reference_minutes_per_match')/ref_total
                if len(starting)==11 and ref_total and all(players[pid].get('reference_minutes_per_match') is not None for pid in starting) else None,
        },
        'qualification':'observed partial counts are lower bounds; anticipated minutes are caller estimates; '
                        'reference bound to raw recent history, not automatically aligned to venue/decay-weighted lambda',
    }


def build_intelligence(payload, cutoff):
    block=payload.get('team_intelligence')
    if block is None:
        return {'schema':SCHEMA,'status':'missing','probability_effect_enabled':False,
                'reason':'未取得球队注册/阵容情报，缺失不等于无外援或零缺阵'}
    if not isinstance(block,dict) or block.get('schema')!=SCHEMA:
        raise ValueError('球队情报schema无效')
    match=block.get('match')
    if not isinstance(match,dict): raise ValueError('球队情报缺比赛绑定')
    for k in ('home','away','competition'):
        if match.get(k)!=payload.get(k): raise ValueError('球队情报比赛身份不匹配:'+k)
    text(match.get('season'),'season')
    if payload.get('season') is not None and match['season']!=payload['season']:
        raise ValueError('球队情报赛季不匹配')
    if aware_datetime(match.get('kickoff_at'),'intel.kickoff_at')!=aware_datetime(payload.get('kickoff_at'),'kickoff_at'):
        raise ValueError('球队情报开球时间不匹配')
    # 拒绝任意嵌套对象里声明的未来可得时间，不能靠顶层旧时间包裹赛后情报。
    def check_nested(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in ('available_at','collected_at','published_at','feature_as_of') and v is not None:
                    if aware_datetime(v,'team_intelligence.'+k)>cutoff:
                        raise ValueError('球队情报晚于快照截止，存在未来泄漏')
                check_nested(v)
        elif isinstance(value,list):
            for v in value: check_nested(v)
    check_nested(block)
    evidence=checked_evidence(block.get('evidence'),cutoff)
    teams=block.get('teams')
    if not isinstance(teams,dict) or set(teams)!=set(('home','away')):
        raise ValueError('球队情报须有主客双方；未知一方可明确设为null')
    result={}
    for side in ('home','away'):
        result[side]=(_side({**teams[side],'_match':match},side,payload,evidence)
                      if isinstance(teams[side],dict) else None)
        if teams[side] is not None and not isinstance(teams[side],dict): raise ValueError('球队情报对象无效')
    return {'schema':SCHEMA,'status':'ok' if all(result.values()) else 'partial',
            'match':dict(match),'teams':result,'evidence_ids':sorted(evidence),
            'source_sha256':{k:e['sha256'] for k,e in evidence.items()},
            'available_at':max(aware_datetime(e['available_at'],'available_at') for e in evidence.values()).isoformat(),
            'input_sha256':digest(block),'probability_effect_enabled':False,
            'provenance_status':'caller_declared_evidence_not_source_authenticated',
            'reference_status':'raw_recent_history_binding_only',
            'production_gate':'coefficients disabled; require prematch ledger and independent walkforward before matrix integration'}
