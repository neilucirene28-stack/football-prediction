"""采集流水线：Source → 校验 → 落库（有 DB 时）/ JSON（无 DB 时）。"""
import json
import os
from datetime import datetime, timezone

from .sources.base import Source
from .sources.demo import DemoSource

try:
    from .sources.xiaodianhuo import XiaoDianHuoSource
    _SOURCES = {"demo": DemoSource, "xiaodianhuo": XiaoDianHuoSource}
except ImportError:  # pragma: no cover
    _SOURCES = {"demo": DemoSource}

try:
    from .sources.titan007 import Titan007Source
    _SOURCES["titan007"] = Titan007Source
except ImportError:  # pragma: no cover
    pass


def get_source(name: str) -> Source:
    cls = _SOURCES.get(name)
    if cls is None:
        raise ValueError(f"未知数据源: {name}，可选: {list(_SOURCES)}")
    return cls()


def save_matches(matches: list[dict], out_dir: str = "data") -> str:
    """无 DB 时的降级：写入 JSON 文件。返回文件路径。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, f"matches-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"collected_at": datetime.now(timezone.utc).isoformat(),
                   "matches": matches}, f, ensure_ascii=False, indent=2)
    return path


def save_to_db(matches: list[dict], database_url: str) -> int:
    """有 DB 时写入 matches 表（upsert 按 external_id+source）。"""
    import psycopg
    n = 0
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for m in matches:
                cur.execute(
                    """INSERT INTO matches
                       (external_id, source, competition, home_team, away_team,
                        kickoff_at, status, raw)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (external_id, source) DO UPDATE SET
                           competition = EXCLUDED.competition,
                           home_team = EXCLUDED.home_team,
                           away_team = EXCLUDED.away_team,
                           kickoff_at = EXCLUDED.kickoff_at,
                           status = EXCLUDED.status,
                           raw = EXCLUDED.raw,
                           collected_at = now()""",
                    (m.get("external_id"), m.get("_source", "demo"),
                     m.get("competition", ""), m["home_team"], m["away_team"],
                     m["kickoff_at"], m.get("status", "scheduled"),
                     json.dumps(m, ensure_ascii=False)))
                n += cur.rowcount or 0
        conn.commit()
    return n
