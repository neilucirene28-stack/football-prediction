"""竞彩牌数的增量WDL验证。离线研究，不被生产预测器导入或自动上线。"""
from collections import Counter, defaultdict
from datetime import timezone
import hashlib
import json
import math

from .jingcai_cards import load_context, predict_cards
from .jingcai_ledger import validate_live_record
from .jingcai_review import decoded, prepared_record, timestamp

SCHEMA = "jingcai-cards-incremental-walkforward-v1"
FEATURES = ("season_yellow_rate_difference", "season_yellow_rate_sum",
            "season_red_rate_difference", "season_red_rate_sum",
            "predicted_red_home", "predicted_red_away")
SPEC = {"ridge": .1, "learning_rate": .1, "iterations": 250,
        "min_train": 200, "min_test": 100, "min_folds": 3,
        "min_team_card_matches": 5, "min_coverage": .8}


def eligible_record(row, asof, context):
    try:
        scored, reason = prepared_record(row, asof)
        if reason:return None, reason
        saved=decoded(row["payload"]);payload=saved["input"];result=saved["result"]
        times=validate_live_record(payload,result,now=scored["predicted_at"],require_hash=True)
        if not row.get("source"):return None,"missing_settlement_source"
        if result.get("p_final_full") is None:return None,"missing_full_probability"
        cards=predict_cards(payload,times["snapshot"],context=context)
        if cards.get("status")!="ok" or cards.get("current_season_totals") is None:
            return None,"missing_current_season_cards"
        stored=result.get("derivatives",{}).get("cards",{})
        if (stored.get("audit",{}).get("data_profile")!=context["profile"] or
            stored.get("audit",{}).get("source_sha256")!=cards["audit"]["source_sha256"] or
            stored.get("audit",{}).get("cutoff_at")!=times["snapshot"].isoformat() or
            stored.get("current_season_totals")!=cards["current_season_totals"] or
            stored.get("red_probabilities_full")!=cards["red_probabilities_full"]):
            return None,"mismatched_card_feature_provenance"
        totals=cards["current_season_totals"]
        h,a=totals["home"],totals["away"]
        if any(t["matches_with_cards"]<SPEC["min_team_card_matches"] or
               t["coverage"]<SPEC["min_coverage"] for t in (h,a)):
            return None,"insufficient_current_card_coverage"
        yh,ya=h["yellow_per_observed_match"],a["yellow_per_observed_match"]
        rh,ra=h["red_per_observed_match"],a["red_per_observed_match"]
        red=cards["red_probabilities_full"]
        features=[yh-ya,yh+ya,rh-ra,rh+ra,red["home"],red["away"]]
        if not all(math.isfinite(v) for v in features):return None,"invalid_card_features"
        return {**scored,"features":features,"snapshot_at":times["snapshot"],
                "season":cards["season"],"score":(row["home_goals"],row["away_goals"])},None
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
        return None,"invalid_card_bound_record"


def _softmax(values):
    shift=max(values);exp=[math.exp(v-shift) for v in values];mass=sum(exp)
    return [v/mass for v in exp]


def _design(features, means, scales):
    # 固定截断仅用于数值稳定，标准化参数只在训练集拟合。
    return [1.0]+[max(-5.0,min(5.0,(x-m)/s)) for x,m,s in zip(features,means,scales)]


def fit(rows, *, use_cards):
    width=len(FEATURES) if use_cards else 0
    means=[sum(r["features"][i] for r in rows)/len(rows) for i in range(width)]
    scales=[max(math.sqrt(sum((r["features"][i]-means[i])**2 for r in rows)/len(rows)),1e-6)
            for i in range(width)]
    weights=[[0.0]*(width+1) for _ in range(3)]
    design=[_design(r["features"],means,scales) for r in rows]
    offsets=[[math.log(max(p,1e-12)) for p in r["probs_full"]] for r in rows]
    for _ in range(SPEC["iterations"]):
        grad=[[0.0]*(width+1) for _ in range(3)]
        for row,x,base in zip(rows,design,offsets):
            probs=_softmax([base[c]+sum(w*v for w,v in zip(weights[c],x)) for c in range(3)])
            for c in range(3):
                error=probs[c]-int(row["outcome"]==c)
                for j,v in enumerate(x):grad[c][j]+=error*v/len(rows)
        for c in range(3):
            for j in range(width+1):
                weights[c][j]-=SPEC["learning_rate"]*(grad[c][j]+SPEC["ridge"]*weights[c][j])
    return {"means":means,"scales":scales,"weights":weights}


def estimate(row, model):
    x=_design(row["features"],model["means"],model["scales"])
    return _softmax([math.log(max(row["probs_full"][c],1e-12))+
                     sum(w*v for w,v in zip(model["weights"][c],x)) for c in range(3)])


def _loss(probs, outcome):
    return (sum((p-int(i==outcome))**2 for i,p in enumerate(probs)),
            -math.log(max(probs[outcome],1e-12)))


def _comparison(observations, first, second):
    deltas=[o[first][0]-o[second][0] for o in observations]
    n=len(deltas)
    if not n:return {"n":0,"brier_delta":None,"logloss_delta":None,"upper_95_normal":None}
    mean=sum(deltas)/n
    se=math.sqrt(sum((d-mean)**2 for d in deltas)/(n*(n-1))) if n>1 else None
    return {"n":n,"brier_delta":mean,
            "logloss_delta":sum(o[first][1]-o[second][1] for o in observations)/n,
            "upper_95_normal":mean+1.96*se if se is not None else None,
            "uncertainty_qualification":"paired normal approximation; match dependence and repeated research not corrected"}


def evaluate(rows, *, asof, test_start):
    asof=timestamp(asof,"asof");test_start=timestamp(test_start,"test_start")
    if test_start>=asof:raise ValueError("测试起点必须早于复盘截止")
    context=load_context();excluded=Counter();groups=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict):excluded["invalid_row"]+=1;continue
        prepared,reason=eligible_record(row,asof,context)
        if reason:excluded[reason]+=1
        else:groups[(prepared["model_version"],prepared["match_id"])].append(prepared)
    selected=[]
    for duplicates in groups.values():
        if len({(r["score"],r["league"]) for r in duplicates})>1:
            excluded["conflicting_physical_match"]+=len(duplicates);continue
        # 可得时间采用同场结算声明中最晚者，避免重复导出将结果提前。
        chosen=min(duplicates,key=lambda r:(r["predicted_at"],r["prediction_id"]))
        chosen["settled_at"]=max(r["settled_at"] for r in duplicates)
        excluded["additional_forecast_same_match_version"]+=len(duplicates)-1
        selected.append(chosen)
    versions=defaultdict(list)
    for row in selected:versions[row["model_version"]].append(row)
    output={}
    for version,cohort in sorted(versions.items()):
        days=defaultdict(list)
        for row in cohort:
            if row["kickoff_at"]>=test_start:
                days[row["kickoff_at"].astimezone(timezone.utc).date()].append(row)
        folds=[];observations=[]
        for day,test in sorted(days.items()):
            cutoff=min(r["snapshot_at"] for r in test)
            kickoff=min(r["kickoff_at"] for r in test)
            ids={r["match_id"] for r in test}
            train=[r for r in cohort if r["settled_at"]<cutoff and r["kickoff_at"]<kickoff and r["match_id"] not in ids]
            fold={"test_day_utc":day.isoformat(),"training_cutoff":cutoff.isoformat(),
                  "train_n":len(train),"test_n":len(test)}
            if len(train)<SPEC["min_train"]:
                fold["status"]="insufficient_training";folds.append(fold);continue
            train.sort(key=lambda r:(r["kickoff_at"],r["match_id"]))
            control=fit(train,use_cards=False);candidate=fit(train,use_cards=True)
            paired=[]
            for row in sorted(test,key=lambda r:r["match_id"]):
                paired.append({"baseline":_loss(row["probs_full"],row["outcome"]),
                               "intercept_control":_loss(estimate(row,control),row["outcome"]),
                               "cards_candidate":_loss(estimate(row,candidate),row["outcome"])})
            fold.update(status="evaluated",training_latest_settlement=max(r["settled_at"] for r in train).isoformat(),
                        candidate_vs_baseline=_comparison(paired,"cards_candidate","baseline"),
                        candidate_vs_intercept=_comparison(paired,"cards_candidate","intercept_control"))
            folds.append(fold);observations.extend(paired)
        comparisons={name:_comparison(observations,"cards_candidate",name) for name in ("baseline","intercept_control")}
        evaluated=[f for f in folds if f["status"]=="evaluated"]
        reasons=[]
        if len(observations)<SPEC["min_test"]:reasons.append("insufficient_test_matches")
        if len(evaluated)<SPEC["min_folds"]:reasons.append("insufficient_temporal_folds")
        for name,comparison in comparisons.items():
            if comparison["upper_95_normal"] is None or comparison["upper_95_normal"]>0:
                reasons.append(f"brier_nonworsening_not_supported_vs_{name}")
            if comparison["logloss_delta"] is None or comparison["logloss_delta"]>0:
                reasons.append(f"logloss_worse_or_unavailable_vs_{name}")
        if any(f["candidate_vs_baseline"]["brier_delta"]>0 or f["candidate_vs_intercept"]["brier_delta"]>0 for f in evaluated):
            reasons.append("some_temporal_fold_brier_worse")
        output[version]={"eligible_matches":len(cohort),"evaluated_matches":len(observations),
                         "folds":folds,"paired_comparisons":comparisons,
                         "research_screen_passed":not reasons,"blocking_reasons":reasons,
                         "production_enabled":False}
    # 与输入内容绑定；摘要不是来源鉴真，也不能证明此次验证未被反复选择。
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str,separators=(",",":")).encode()).hexdigest()
    return {"schema":SCHEMA,"scope":"jingcai_only","status":"ok" if selected else "no_eligible_data",
            "asof":asof.isoformat(),"test_start":test_start.isoformat(),"spec":dict(SPEC),
            "features":list(FEATURES),"input_sha256":digest,"input_rows":len(rows),
            "eligible_matches":len(selected),"excluded":dict(excluded),"by_version":output,
            "production_enabled":False,"production_gate":"never automatic; independent locked holdout and matrix-consistent integration still required",
            "qualification":"prematch card association research; not causal red-card effects, not player absence effect; no goal mean or live model changed"}
