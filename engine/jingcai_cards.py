"""竞彩牌数：精确球队绑定、当前赛季数量、时间截止与历史先验标识。"""
from datetime import date
import json
import hashlib
import math
from pathlib import Path

from .cards import SHRINK_PRIOR, REF_MIN_MATCHES, REF_WEIGHT
from .jingcai_inputs import aware_datetime

CARDS_REVISION = "jingcai-cards-season-cutoff-exact-binding-v1"
FIELDS = ("mp", "yf", "ya", "rf", "ra")


def load_context():
    history_bytes=Path(__file__).with_name("cards_history.json").read_bytes()
    alias_bytes=Path(__file__).with_name("cards_alias.json").read_bytes()
    return {"history":json.loads(history_bytes),"aliases":json.loads(alias_bytes),
        "profile":{"revision":CARDS_REVISION,
            "historical_prior_sha256":hashlib.sha256(history_bytes).hexdigest(),
            "team_aliases_sha256":hashlib.sha256(alias_bytes).hexdigest(),
            "shrink_prior":SHRINK_PRIOR,"ref_min_matches":REF_MIN_MATCHES,"ref_weight":REF_WEIGHT,
            "recent_window":5,"recent_shrink":3.0,"recent_weight":.15}}


def count(value, name):
    if isinstance(value,bool) or not isinstance(value,int) or value < 0:
        raise ValueError(f"{name}必须为非负整数")
    try:
        if not math.isfinite(float(value)):raise ValueError(f"{name}过大")
    except OverflowError as exc:raise ValueError(f"{name}过大") from exc
    return value


def team_counts(team, name):
    if not isinstance(team,dict):raise ValueError(f"{name}必须为对象")
    for side in ("home","away"):
        block=team.get(side)
        if not isinstance(block,dict):raise ValueError(f"{name}.{side}缺失")
        for key in FIELDS:count(block.get(key),f"{name}.{side}.{key}")
        if block["mp"]==0 and any(block[key] for key in FIELDS[1:]):
            raise ValueError("零场次不能包含牌数")
    return team


def _resolve(name, teams, aliases):
    if name in teams:return name,"exact_source_name"
    mapped=aliases.get(name)
    if mapped in teams:return mapped,"bundled_exact_alias_declared_not_independently_verified"
    return None,None


def _totals(team):
    h,a=team["home"],team["away"]
    observed=h["mp"]+a["mp"]
    played=team.get("matches_played",observed)
    count(played,"matches_played")
    if played<observed:raise ValueError("已观测牌数场数超过出场数")
    yellow=h["yf"]+a["yf"];red=h["rf"]+a["rf"]
    return {"matches_played":played,"matches_with_cards":observed,
            "yellow_observed":yellow,"red_observed":red,
            "yellow_per_observed_match":yellow/observed if observed else None,
            "red_per_observed_match":red/observed if observed else None,
            "coverage":observed/played if played else None,
            "totals_complete":observed==played and played>0,
            "coverage_scope":"eligible_imported_completed_results_only",
            "official_season_completeness_verified":False,
            "red_subtypes":"not_separately_identified"}


def _rate(team, side, base_y, base_r):
    t=team[side];n=t["mp"]
    y=(t["yf"]+base_y*SHRINK_PRIOR)/(n+SHRINK_PRIOR)
    make=(t["ya"]+base_y*SHRINK_PRIOR)/(n+SHRINK_PRIOR)
    red=(t["rf"]+base_r*SHRINK_PRIOR)/(n+SHRINK_PRIOR)
    return y/base_y if base_y else 1,make/base_y if base_y else 1,red


def _over(lam,line):
    if lam<=0:return 0.0
    return max(0.0,min(1.0,1-sum(math.exp(-lam+i*math.log(lam)-math.lgamma(i+1))
                                for i in range(int(line)+1))))


def predict_cards(payload, cutoff, *, context=None):
    spec=payload.get("cards") or {}
    if not isinstance(spec,dict):raise ValueError("cards必须为对象")
    code=spec.get("league_code")
    if not code:return {"status":"insufficient_data","reason":"未指定牌数联赛代码"}
    context=context or load_context()
    history=context["history"];prior=history.get("leagues",{}).get(code)
    artifact=spec.get("season_stats")
    current=False;season_totals=None;seasons=history.get("seasons",[])
    audit={"model_revision":CARDS_REVISION,"data_profile":context["profile"],"cutoff_at":cutoff.isoformat(),
           "recent_excluded":{},"source_lineage":"declared_not_independently_authenticated"}
    end_years=[2000+int(s[-2:]) for s in seasons if isinstance(s,str) and len(s)==4 and s.isdigit()]
    if not end_years or cutoff.date()<date(max(end_years),7,1):
        prior=None
        audit["historical_prior_status"]="not_used_unavailable_or_future_coverage"
    if artifact is not None:
        if not isinstance(artifact,dict) or artifact.get("schema")!="jingcai-cards-season-v1" or artifact.get("project")!="jingcai":
            raise ValueError("牌数数据必须为竞彩赛季统计包")
        if not spec.get("season") or artifact.get("season")!=spec["season"]:
            raise ValueError("牌数赛季与预测要求不匹配")
        start=date.fromisoformat(artifact["season_start"]);end=date.fromisoformat(artifact["season_end"])
        kickoff=aware_datetime(payload["kickoff_at"],"kickoff_at")
        if start>end or not start<=kickoff.date()<=end:
            raise ValueError("赛季范围未覆盖本场比赛")
        available=aware_datetime(artifact.get("available_at"),"cards.available_at")
        if available>cutoff:raise ValueError("牌数统计晚于赛前快照")
        if not artifact.get("source") or not artifact.get("source_sha256"):
            raise ValueError("赛季牌数缺来源或原件摘要")
        import re
        if not re.fullmatch(r"[0-9a-f]{64}",str(artifact["source_sha256"])):
            raise ValueError("牌数原件摘要无效")
        if not isinstance(artifact.get("leagues"),dict):raise ValueError("赛季联赛集合必须为对象")
        league=artifact["leagues"].get(code)
        if not isinstance(league,dict):raise ValueError("当前赛季包缺指定联赛")
        current=True
        audit.update(season=spec["season"],available_at=available.isoformat(),
                     source=artifact["source"],source_sha256=artifact["source_sha256"])
    else:
        # 未分赛季聚合无法从中扣除未来赛季；截止早于其完整覆盖则禁用。
        if not prior or not end_years or cutoff.date()<date(max(end_years),7,1):
            return {"status":"insufficient_data","reason":"无可用赛季统计或历史聚合覆盖了未来赛季"}
        league=prior
        audit.update(historical_prior_seasons=seasons,
                     qualification="historical pooled prior; not current-season totals or authenticated historical snapshot")
    if payload.get("competition") and payload["competition"]!=league.get("name"):
        return {"status":"insufficient_data","reason":"当前赛事与牌数联赛未精确绑定，不能混用联赛或杯赛统计","audit":audit}
    teams=league.get("teams",{})
    if not isinstance(teams,dict):raise ValueError("牌数球队集合必须为对象")
    if current:
        matches=count(league.get("matches"),"league.matches")
        for name,team in teams.items():team_counts(team,str(name))
        for side in ("home","away"):
            if sum(t[side]["mp"] for t in teams.values())!=matches:
                raise ValueError("联赛与球队已观测场数不一致")
            for key,metric in (("yf","yellow"),("rf","red")):
                mean=league.get(f"{side}_{metric}")
                if isinstance(mean,bool) or not isinstance(mean,(int,float)) or not math.isfinite(mean) or mean<0:
                    raise ValueError("联赛牌数均值无效")
                observed=sum(t[side][key] for t in teams.values())
                if not math.isclose(observed,mean*matches,rel_tol=1e-10,abs_tol=1e-6):
                    raise ValueError("联赛均值与球队牌数总和不一致")
    aliases=context["aliases"].get(code,{})
    home,bh=_resolve(payload.get("home"),teams,aliases);away,ba=_resolve(payload.get("away"),teams,aliases)
    if not home or not away or home==away:
        return {"status":"insufficient_data","reason":"球队未精确绑定到牌数统计","audit":audit}
    ht=team_counts(teams[home],"home_team");at=team_counts(teams[away],"away_team")
    audit["team_binding"]={"home":{"source_team":home,"basis":bh},"away":{"source_team":away,"basis":ba}}
    suspensions=[];suspension_excluded=[]
    declared=spec.get("confirmed_suspensions") or []
    if not isinstance(declared,list):raise ValueError("confirmed_suspensions必须为列表")
    for row in declared:
        try:
            if not isinstance(row,dict) or row.get("status")!="confirmed" or row.get("team") not in (home,away):
                raise ValueError("unconfirmed_or_unbound")
            if row.get("competition")!=payload.get("competition"):
                raise ValueError("different_competition")
            if aware_datetime(row.get("kickoff_at"),"suspension.kickoff_at")!=aware_datetime(payload["kickoff_at"],"kickoff_at"):
                raise ValueError("different_fixture")
            if aware_datetime(row.get("available_at"),"suspension.available_at")>cutoff:
                raise ValueError("future_availability")
            if not row.get("source") or not row.get("player_id") or not row.get("player_name"):
                raise ValueError("missing_source_or_player")
            suspensions.append(dict(row))
        except (ValueError,TypeError,KeyError) as exc:suspension_excluded.append(str(exc))
    # 同一联赛历史聚合只作收缩先验，当前赛季数量单独显示。
    base={}
    matches=count(league.get("matches"),"league.matches")
    for key in ("home_yellow","away_yellow","home_red","away_red"):
        rate=league.get(key)
        if isinstance(rate,bool) or not isinstance(rate,(int,float)) or not math.isfinite(rate) or rate<0:
            raise ValueError("联赛牌数均值无效")
        anchor=prior.get(key,rate) if prior else rate
        base[key]=(rate*matches+anchor*SHRINK_PRIOR)/(matches+SHRINK_PRIOR) if current else rate
    if base["home_yellow"]<=0 or base["away_yellow"]<=0:
        return {"status":"insufficient_data","reason":"缺可用黄牌基线","audit":audit}
    hs=_rate(ht,"home",base["home_yellow"],base["home_red"])
    aws=_rate(at,"away",base["away_yellow"],base["away_red"])
    lam_h=base["home_yellow"]*hs[0]*aws[1];lam_a=base["away_yellow"]*aws[0]*hs[1]
    lam_rh=hs[2];lam_ra=aws[2]
    factors={"home":1.0,"away":1.0};recent_counts={"home":0,"away":0}
    recs=spec.get("recent_records") or []
    if not isinstance(recs,list):raise ValueError("cards.recent_records必须为列表")
    eligible=[]
    from collections import Counter
    excluded=Counter()
    for row in recs:
        try:
            if not isinstance(row,dict):raise ValueError("invalid_record")
            if not current or row.get("season")!=spec["season"] or row.get("league")!=code:
                raise ValueError("season_or_league_mismatch")
            if row.get("team") not in (home,away):raise ValueError("unbound_team")
            completed=aware_datetime(row.get("completed_at"),"cards.completed_at")
            available=aware_datetime(row.get("available_at"),"cards.recent.available_at")
            if completed>available or available>cutoff:raise ValueError("late_or_invalid_time")
            if not start<=completed.date()<=end:raise ValueError("outside_season")
            count(row.get("yellow"),"recent.yellow");count(row.get("red"),"recent.red")
            mid=row.get("match_id")
            if isinstance(mid,bool) or not isinstance(mid,(int,str)) or not str(mid).strip() or (isinstance(mid,int) and mid<=0):
                raise ValueError("missing_or_invalid_match_id")
            eligible.append((completed,row))
        except (ValueError,TypeError,KeyError) as exc:excluded[str(exc)]+=1
    for side,name in (("home",home),("away",away)):
        signatures={};conflicting=set()
        for completed,row in eligible:
            if row["team"]!=name:continue
            identity=str(row["match_id"])
            signature=(completed,row["yellow"],row["red"])
            if identity in signatures and signatures[identity]!=signature:conflicting.add(identity)
            signatures[identity]=signature
        seen=set();selected=[]
        for _,row in sorted(eligible,key=lambda v:v[0],reverse=True):
            if row["team"]!=name:continue
            identity=str(row["match_id"])
            if identity in conflicting:excluded["conflicting_team_match"]+=1;continue
            if identity in seen:excluded["duplicate_team_match"]+=1;continue
            seen.add(identity);selected.append(row)
        selected=selected[:5];recent_counts[side]=len(selected)
        baseline=(base["home_yellow"]+base["away_yellow"])/2
        if selected:
            shrunk=(sum(r["yellow"] for r in selected)+baseline*3)/(len(selected)+3)
            factors[side]=.85+.15*shrunk/baseline
    lam_h*=factors["home"];lam_a*=factors["away"]
    audit["recent_excluded"]=dict(excluded)
    ref_name=spec.get("referee")
    refs=league.get("referees",{})
    if not isinstance(refs,dict):raise ValueError("裁判集合必须为对象")
    if ref_name is not None and not isinstance(ref_name,str):raise ValueError("裁判名必须为字符串")
    ref=refs.get(ref_name)
    ref_info={"name":ref_name,"used":False,"basis":"current_season" if current else "historical_prior"}
    if ref:
        if not isinstance(ref,dict):raise ValueError("裁判统计必须为对象")
        n=count(ref.get("mp"),"referee.mp");yc=count(ref.get("cards"),"referee.cards");rc=count(ref.get("reds"),"referee.reds")
        if n>=REF_MIN_MATCHES:
            confidence=min((n-REF_MIN_MATCHES)/15,1)
            mult=1+((yc/n)/(base["home_yellow"]+base["away_yellow"])-1)*confidence*REF_WEIGHT
            rb=base["home_red"]+base["away_red"]
            red_mult=1+((rc/n)/rb-1)*confidence*REF_WEIGHT if rb else 1
            lam_h*=mult;lam_a*=mult;lam_rh*=red_mult;lam_ra*=red_mult
            ref_info.update(used=True,matches=n,multiplier=mult,red_multiplier=red_mult)
    total=lam_h+lam_a
    ph=-math.expm1(-lam_rh);pa=-math.expm1(-lam_ra);pr=-math.expm1(-lam_rh-lam_ra)
    if current:season_totals={"home":_totals(ht),"away":_totals(at)}
    return {"status":"ok","league":league.get("name",code),"season":spec.get("season"),
            "data_basis":"current_season_with_historical_shrinkage" if current else "historical_prior_only",
            "current_season_totals":season_totals,"audit":audit,
            "confirmed_suspensions":{"records":suspensions,"excluded":suspension_excluded,
                "match_result_effect":"not_applied_without_validated_player_effect; do not infer bans from team card totals"},
            "exp_home_yellow":round(lam_h,2),"exp_away_yellow":round(lam_a,2),"exp_total_yellow":round(total,2),
            "expected_yellow_full":{"home":lam_h,"away":lam_a,"total":total},
            "p_over_3_5":round(_over(total,3.5),4),"p_over_4_5":round(_over(total,4.5),4),
            "yellow_over_probabilities_full":{"3.5":_over(total,3.5),"4.5":_over(total,4.5)},
            "p_red":round(pr,4),"p_home_red":round(ph,4),"p_away_red":round(pa,4),
            "red_probabilities_full":{"any":pr,"home":ph,"away":pa},
            "exp_booking_points":round(10*total+25*(lam_rh+lam_ra),1),
            "referee":ref_info,"recent_form":{"factors":factors,"matches":recent_counts},
            "confidence":"low" if not current or min(ht['home']['mp'],at['away']['mp'])<5 or not ref_info['used'] else "medium",
            "red_card_scenarios":{"none":(1-ph)*(1-pa),"home_only":ph*(1-pa),"away_only":(1-ph)*pa,"both":ph*pa,
                "assumption":"independent team red-card Poisson counts; descriptive risk only"},
            "match_result_integration":{"status":"not_applied","reason":"requires validated conditional impact model; avoid double counting red-card effects already in historical goals"},
            "warnings":[] if current else ["当前赛季牌数缺失；旧数据仅作历史先验，不代表当前赛季数量"]}
