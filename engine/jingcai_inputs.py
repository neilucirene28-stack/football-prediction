"""竞彩入口校验；仅核验传入证据，不把未注明时间的数据当作赛前封存。"""
from datetime import date, datetime, time
import math


OUTCOMES = ("home", "draw", "away")
TIME_FIELDS = ("available_at", "collected_at", "feature_as_of")


def finite_number(value, path, *, minimum=None, maximum=None, strict_min=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{path} 必须是有限数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{path} 必须是有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{path} 必须是有限数值")
    if minimum is not None and (number <= minimum if strict_min else number < minimum):
        raise ValueError(f"{path} 数值超出下界")
    if maximum is not None and number > maximum:
        raise ValueError(f"{path} 数值超出上界")
    return number


def aware_datetime(value, path):
    if not isinstance(value, str):
        raise ValueError(f"{path} 必须是带时区的ISO时间")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path} 时间格式无效") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{path} 时间必须带时区")
    return dt


def validate_config(cfg):
    bounds = {
        "rho": (None, None, False), "decay": (0, 1, True),
        "ht_factor": (0, 1, True), "kelly_fraction": (0, 1, False),
        "model_edge": (None, None, False), "shrink_prior": (0, None, False),
        "weak_shrink_prior": (0, None, False), "weak_goal_cap": (0, None, False),
        "letdraw_strength": (0, 1, False), "divergence_gate": (0, 1, False),
        "mc_min_score": (0, None, False),
    }
    for key, (lo, hi, strict) in bounds.items():
        cfg[key] = finite_number(cfg[key], f"config.{key}", minimum=lo,
                                 maximum=hi, strict_min=strict)
    if cfg["ht_factor"] >= 1:
        raise ValueError("config.ht_factor 必须小于1")
    n = cfg["mc_n"]
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ValueError("config.mc_n 必须为正整数")
    if cfg.get("platt") is not None:
        platt = cfg["platt"]
        if not isinstance(platt, dict) or any(k not in platt for k in OUTCOMES):
            raise ValueError("config.platt 必须含home/draw/away参数")
        normalized = {}
        for key in OUTCOMES:
            pair = platt[key]
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"config.platt.{key} 必须含两个参数")
            normalized[key] = [finite_number(v, f"config.platt.{key}") for v in pair]
        cfg["platt"] = normalized


def validate_payload(payload, cutoff):
    """返回复制后的输入和时间覆盖审计；从不修改调用方数据。"""
    p = dict(payload)
    audit = {"status": "declared_inputs_checked_source_lineage_unverified",
             "cutoff_at": cutoff.isoformat(), "history": {},
             "excluded_optional_signals": [], "feature_timestamps_checked": []}

    def check_times(block, path):
        for key in TIME_FIELDS:
            if block.get(key) is not None:
                stamp = aware_datetime(block[key], f"{path}.{key}")
                if stamp > cutoff:
                    raise ValueError(f"{path}.{key} 晚于快照截止，存在未来泄漏")
                audit["feature_timestamps_checked"].append(f"{path}.{key}")

    check_times(p, "payload")
    declared = p.get("feature_timestamps")
    if declared is not None:
        if not isinstance(declared, dict):
            raise ValueError("feature_timestamps 必须为对象")
        for key, value in declared.items():
            stamp = aware_datetime(value, f"feature_timestamps.{key}")
            if stamp > cutoff:
                raise ValueError(f"feature_timestamps.{key} 晚于快照截止，存在未来泄漏")
            audit["feature_timestamps_checked"].append(f"feature_timestamps.{key}")

    p["league_avg_goals"] = finite_number(p.get("league_avg_goals", 2.7),
                                         "league_avg_goals", minimum=0, strict_min=True)
    for key in ("home_recent", "away_recent", "h2h"):
        rows = p.get(key)
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ValueError(f"{key} 必须为列表")
        normalized, stamps = [], []
        missing = date_only = missing_completed = invalid_venue = 0
        for idx, row in enumerate(rows):
            path = f"{key}[{idx}]"
            if not isinstance(row, dict):
                raise ValueError(f"{path} 必须为对象")
            row = dict(row)
            for field in ("gf", "ga"):
                v = row.get(field)
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise ValueError(f"{path}.{field} 必须为非负整数进球")
            for field in ("opp_attack", "opp_defense"):
                if field in row:
                    row[field] = finite_number(row[field], f"{path}.{field}", minimum=0, strict_min=True)
            # 历史回填使用home_away；统一到引擎消费的venue字段。
            if not row.get("venue") and row.get("home_away") in ("H", "A", "N"):
                row["venue"] = row["home_away"]
            if row.get("venue") not in (None, "H", "A", "N"):
                row.pop("venue", None)
                invalid_venue += 1
            check_times(row, path)
            completed = None
            if row.get("completed_at") is not None:
                completed = aware_datetime(row["completed_at"], f"{path}.completed_at")
                if completed > cutoff:
                    raise ValueError(f"{path} 完赛时间晚于快照截止，存在未来泄漏")
                for field in ("available_at", "collected_at"):
                    if row.get(field) is not None and aware_datetime(row[field], f"{path}.{field}") < completed:
                        raise ValueError(f"{path}.{field} 早于完赛，不能记录最终比分")
            else:
                missing_completed += 1
            # kickoff_at/played_at是比赛发生时间，不能证明此时已有最终比分。
            stamp = None
            values = [(field, row[field]) for field in ("date", "played_at", "kickoff_at")
                      if row.get(field) is not None]
            for field, value in values:
                if isinstance(value, str) and len(value) == 10:
                    try:
                        day = date.fromisoformat(value)
                    except ValueError as exc:
                        raise ValueError(f"{path}.{field} 日期格式无效") from exc
                    local_day = cutoff.date()
                    if day > local_day or (day == local_day and completed is None):
                        raise ValueError(f"{path} 历史日期不早于快照；当天结果须提供completed_at")
                    current = datetime.combine(day, time.min, cutoff.tzinfo)
                    date_only += int(field == "date")
                else:
                    current = aware_datetime(value, f"{path}.{field}")
                    if current >= cutoff:
                        raise ValueError(f"{path}.{field} 不早于快照截止，存在未来泄漏")
                    if current.astimezone(cutoff.tzinfo).date() == cutoff.date() and completed is None:
                        raise ValueError(f"{path} 当天历史结果须提供completed_at")
                if completed is not None and current > completed:
                    raise ValueError(f"{path} 完赛时间早于比赛时间")
                if stamp is None:
                    stamp = current
            if stamp is None:
                stamp = completed
            if stamp is None:
                missing += 1
            stamps.append(stamp)
            normalized.append(row)
        # 日期不全时保留原序，不猜测缺日期行的位置。
        if normalized and missing == 0:
            order = sorted(range(len(stamps)), key=lambda i: stamps[i], reverse=True)
            normalized = [normalized[i] for i in order]
            ordering = "date_descending"
        else:
            ordering = "input_order_unverified" if normalized else "empty"
        p[key] = normalized
        audit["history"][key] = {"rows": len(rows), "missing_match_time": missing,
                                 "date_only_rows": date_only, "ordering": ordering,
                                 "missing_completed_at": missing_completed,
                                 "invalid_venue_treated_as_unknown": invalid_venue}

    for key in ("odds", "opening_odds", "elo", "asian", "injury"):
        block = p.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise ValueError(f"{key} 必须为对象")
        block = dict(block)
        check_times(block, key)
        if key in ("odds", "opening_odds", "elo") and block:
            for outcome in OUTCOMES if key != "elo" else ("home", "away"):
                block[outcome] = finite_number(block.get(outcome), f"{key}.{outcome}",
                                              minimum=1 if key != "elo" else None,
                                              strict_min=key != "elo")
        if key == "asian":
            for field in ("handicap", "opening_handicap", "home_water", "opening_home_water"):
                if block.get(field) is not None:
                    block[field] = finite_number(block[field], f"asian.{field}",
                                                 minimum=0 if "water" in field else None)
        if key == "injury":
            for field in ("home_attack", "away_attack", "home_defense", "away_defense"):
                if field in block:
                    block[field] = finite_number(block[field], f"injury.{field}", minimum=0, strict_min=True)
        p[key] = block
    if p.get("ou_line") is not None:
        p["ou_line"] = finite_number(p["ou_line"], "ou_line", minimum=0)
    # 保留旧接口对可选辅助信号的容错：坏信号排除并留痕，不进入概率计算。
    for key in ("handicap_sp", "af_pred"):
        values = p.get(key)
        if values is None:
            continue
        try:
            if not isinstance(values, (list, tuple)) or len(values) != 3:
                raise ValueError("须有三个数值")
            values = [finite_number(v, key, minimum=1 if key == "handicap_sp" else 0,
                                    maximum=1 if key == "af_pred" else None,
                                    strict_min=key == "handicap_sp") for v in values]
            if key == "af_pred" and sum(values) <= 0:
                raise ValueError("概率全为零")
            p[key] = values
        except ValueError as exc:
            p[key] = None
            audit["excluded_optional_signals"].append({"field": key, "reason": str(exc)})
    def has_availability(value):
        if isinstance(value, dict):
            return bool(value.get("available_at"))
        if isinstance(value, list):
            return bool(value) and all(isinstance(row, dict) and row.get("available_at") for row in value)
        return False

    audit["features_without_available_at"] = [
        key for key in ("home_recent", "away_recent", "h2h", "odds", "opening_odds",
                        "elo", "asian", "injury", "af_pred", "handicap_sp", "league_avg_goals")
        if p.get(key) and key not in (declared or {}) and
        not has_availability(p[key])]
    return p, audit
