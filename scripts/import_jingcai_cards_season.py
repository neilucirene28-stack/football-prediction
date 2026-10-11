#!/usr/bin/env python3
"""从本地赛季CSV建立竞彩牌数统计；不联网、不倒填采集时间、不补零缺失牌数。"""
import argparse
from collections import Counter
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def import_season(raw, *, league_code, league_name, season, start, end, source,
                  source_timezone, observed_at=None):
    observed_at=observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("采集时间缺时区")
    if not source or not season or not league_code:raise ValueError("来源、联赛和赛季必须明确")
    start=date.fromisoformat(start);end=date.fromisoformat(end)
    if start>end:raise ValueError("赛季起止无效")
    cutoff=observed_at.astimezone(ZoneInfo(source_timezone)).date()
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    teams={};refs={};excluded=Counter();seen={};accepted=0;yc_h=yc_a=rc_h=rc_a=0
    fields=('Date','HomeTeam','AwayTeam','FTHG','FTAG','HY','AY','HR','AR')
    if rows and any(k not in rows[0] for k in fields):raise ValueError("CSV缺牌数或比赛必需列")
    def block():return {'mp':0,'yf':0,'ya':0,'rf':0,'ra':0}
    def team(name):
        return teams.setdefault(name,{'home':block(),'away':block(),'matches_played':0})
    def integer(s):
        if not isinstance(s,str) or not s.strip().isdigit():raise ValueError("missing_or_invalid_count")
        return int(s)
    parsed=[]
    for row in rows:
        try:
            ds=row['Date'].strip()
            try:day=datetime.strptime(ds,'%d/%m/%Y').date()
            except ValueError:
                try:day=datetime.strptime(ds,'%d/%m/%y').date()
                except ValueError:day=date.fromisoformat(ds)
            if not start<=day<=end:raise ValueError("outside_season")
            # 日期不足以证明今日比赛已完成；只接受已结束的来源日。
            if day>=cutoff:raise ValueError("same_source_day_or_future")
            integer(row['FTHG']);integer(row['FTAG'])
            h=row['HomeTeam'].strip();a=row['AwayTeam'].strip()
            if not h or not a or h==a:raise ValueError("invalid_teams")
            if row.get('Div') and row['Div']!=league_code:raise ValueError("wrong_league")
            identity=(day.isoformat(),h,a)
            signature=tuple(row.get(k) for k in fields)
            if identity in seen:
                if seen[identity]!=signature:raise ValueError("conflicting_match_record")
                excluded['duplicate_match']+=1;continue
            seen[identity]=signature;parsed.append((identity,row))
        except (ValueError,KeyError) as exc:excluded[str(exc)]+=1
    if excluded['conflicting_match_record']:
        raise ValueError("CSV包含同场冲突记录，不能自动选取")
    for (_,h,a),row in parsed:
        th=team(h);ta=team(a);th['matches_played']+=1;ta['matches_played']+=1
        try:hy,ay,hr,ar=[integer(row[k]) for k in ('HY','AY','HR','AR')]
        except ValueError:
            excluded['match_missing_card_counts']+=1;continue
        for t,side,yf,ya,rf,ra in ((th,'home',hy,ay,hr,ar),(ta,'away',ay,hy,ar,hr)):
            t[side]['mp']+=1;t[side]['yf']+=yf;t[side]['ya']+=ya;t[side]['rf']+=rf;t[side]['ra']+=ra
        accepted+=1;yc_h+=hy;yc_a+=ay;rc_h+=hr;rc_a+=ar
        ref=row.get('Referee','').strip()
        if ref:
            r=refs.setdefault(ref,{'mp':0,'cards':0,'reds':0});r['mp']+=1;r['cards']+=hy+ay;r['reds']+=hr+ar
    if accepted==0:raise ValueError("CSV没有可用的已完成日期且牌数完整的比赛")
    league={'name':league_name,'matches':accepted,'matches_played':len(parsed),
            'home_yellow':yc_h/accepted,'away_yellow':yc_a/accepted,
            'home_red':rc_h/accepted,'away_red':rc_a/accepted,'teams':teams,'referees':refs,
            'league_totals_observed':{'home_yellow':yc_h,'away_yellow':yc_a,'home_red':rc_h,'away_red':rc_a}}
    return {'project':'jingcai','schema':'jingcai-cards-season-v1','season':season,
            'season_start':start.isoformat(),'season_end':end.isoformat(),
            'available_at':observed_at.isoformat(),'source':source,
            'source_sha256':hashlib.sha256(raw).hexdigest(),'source_timezone':source_timezone,
            'leagues':{league_code:league},'audit':{'input_rows':len(rows),'accepted_card_matches':accepted,
                'excluded':dict(excluded),'identity':'exact source team names; no fuzzy merge',
                'count_convention':'as reported in HY/AY/HR/AR; direct reds and second yellows not separately identifiable',
                'qualification':'local source import observed now; not independently authenticated prematch archive'}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--league-code',required=True);p.add_argument('--league-name',required=True)
    p.add_argument('--season',required=True);p.add_argument('--season-start',required=True);p.add_argument('--season-end',required=True)
    p.add_argument('--source',required=True);p.add_argument('--source-timezone',required=True)
    args=p.parse_args()
    result=import_season(args.input.read_bytes(),league_code=args.league_code,league_name=args.league_name,
        season=args.season,start=args.season_start,end=args.season_end,source=args.source,source_timezone=args.source_timezone)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'audit':result['audit']},ensure_ascii=False))


if __name__=='__main__':main()
