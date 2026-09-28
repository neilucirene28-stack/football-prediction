# FastAPI 数据接收后端入口（针对性修正版）
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse

from .config import settings
from .db import check_db, close_db, init_db
from .schemas import IngestionSource
from .services.ingestion import IngestError, process_ingest
from .services.source_detection import resolve_source
from .services.readiness import coverage_report
from .services.detail_modules import collector_v2_modules

logger = logging.getLogger("football-prediction-api")

APP_VERSION = "0.1.0"
SERVICE_NAME = "football-prediction-api"

MAX_INGEST_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("database connection initialized")
    try:
        yield
    finally:
        close_db()
        logger.info("database connection closed")


app = FastAPI(title="Football Prediction Ingestion API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    if check_db():
        return {"status": "ok", "database": "ok"}
    return JSONResponse(status_code=503, content={"status": "error", "database": "error"})


@app.get("/api/v1/status")
def status() -> dict:
    db_state = "ok" if check_db() else "error"
    return {
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "database": db_state,
    }


async def _read_stream_limited(req: Request) -> bytes:
    cl = req.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    body = bytearray()
    async for chunk in req.stream():
        body.extend(chunk)
        if len(body) > MAX_INGEST_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
    return bytes(body)


def _parse_json_body(raw: bytes):
    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    if not isinstance(parsed, (dict, list)):
        raise HTTPException(status_code=400, detail="top-level json must be object or array")
    return parsed


def _run_process(raw: bytes, parsed, src: str, content_type: str) -> dict:
    try:
        result = process_ingest(raw, parsed, src, content_type)
        if result.status == "duplicate":
            return {
                "status": "duplicate",
                "source": result.source,
                "sha256": result.sha256,
                "snapshot_id": result.snapshot_id,
            }
        return result.as_dict()
    except IngestError:
        logger.error("ingest internal error")
        raise HTTPException(status_code=500, detail="ingest failed")
    except Exception:
        logger.exception("ingest unexpected error")
        raise HTTPException(status_code=500, detail="ingest failed")


def _check_upload_token(req: Request):
    """上传接口访问控制：必须携带有效令牌（X-Upload-Token 头），防止公网匿名写入。"""
    import os as _os
    import secrets as _secrets
    # Fail closed until an authenticated HTTPS ingress is configured and reviewed.
    # Never treat untrusted X-Forwarded-Proto as proof of TLS.
    if _os.environ.get("UPLOADS_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=503, detail="upload disabled until HTTPS is configured")
    if req.url.scheme != "https":
        raise HTTPException(status_code=403, detail="HTTPS required for upload")
    expected = _os.environ.get("UPLOAD_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="upload disabled")
    provided = req.headers.get("x-upload-token", "")
    if not _secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized upload")


@app.post("/api/v1/ingest")
async def ingest(req: Request, source: IngestionSource = IngestionSource.UNKNOWN) -> dict:
    _check_upload_token(req)
    raw = await _read_stream_limited(req)
    parsed = _parse_json_body(raw)
    try:
        resolved = resolve_source(parsed, None if source == IngestionSource.UNKNOWN else source.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _run_process(raw, parsed, resolved, req.headers.get("content-type", ""))


@app.post("/api/v1/ingest/file")
async def ingest_file(request: Request, file: UploadFile = File(...), source: IngestionSource = IngestionSource.UNKNOWN) -> dict:
    _check_upload_token(request)
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="only .json files are allowed")
    data = bytearray()
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_INGEST_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
    raw = bytes(data)
    parsed = _parse_json_body(raw)
    try:
        resolved = resolve_source(parsed, None if source == IngestionSource.UNKNOWN else source.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _run_process(raw, parsed, resolved, file.content_type or "")


# ---------- 受控上传入口（新版专用，与公开接口隔离） ----------
from .normalizer.normalizer import normalize_collection_snapshot


@app.post("/api/v1/collection/upload")
async def collection_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    source: IngestionSource = IngestionSource.UNKNOWN,
) -> dict:
    """Authenticated HTTPS batch upload; each file gets its own truthful outcome."""
    _check_upload_token(request)
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    if len(files) > 30:
        raise HTTPException(status_code=413, detail="too many files")
    out = []
    for f in files:
        name = (f.filename or "")[:180]
        if not name.lower().endswith(".json"):
            out.append({"file": name, "status": "rejected", "reason": "only .json allowed"})
            continue
        data = bytearray()
        too_large = False
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_INGEST_BYTES:
                too_large = True
                break
        if too_large:
            out.append({"file": name, "status": "rejected", "reason": "payload too large"})
            continue
        raw = bytes(data)
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            out.append({"file": name, "status": "rejected", "reason": "invalid json"})
            continue
        try:
            src = resolve_source(parsed, None if source == IngestionSource.UNKNOWN else source.value)
        except ValueError as exc:
            out.append({"file": name, "status": "pending_identification", "reason": str(exc)})
            continue
        try:
            result = process_ingest(raw, parsed, src, f.content_type or "")
        except Exception:
            logger.exception("collection upload ingest error file=%s", name)
            out.append({"file": name, "status": "failed", "reason": "raw storage failed"})
            continue
        item = {"file": name, "source": src, "snapshot_id": result.snapshot_id}
        if result.status == "duplicate":
            item["status"] = "duplicate"
            out.append(item)
            continue
        try:
            normalized = normalize_collection_snapshot(result.snapshot_id)
        except Exception:
            logger.exception("collection upload normalize error file=%s", name)
            item.update(status="pending_review", reason="normalization failed; raw snapshot retained")
            out.append(item)
            continue
        actions = [r.as_dict() for r in normalized]
        item["normalization"] = actions
        if actions and all(r.get("action") in ("linked", "created", "already_normalized") for r in actions):
            item["status"] = "classified"
        elif actions and any(r.get("action") == "pending_review" for r in actions):
            item["status"] = "pending_review"
        else:
            item["status"] = "failed"
            item["reason"] = "no match was classified"
        out.append(item)
    return {"results": out}


# ---------- 比赛数据展示接口 ----------
import os
from fastapi.responses import FileResponse
from .db import get_pool


@app.get("/api/v1/matches")
def list_matches() -> dict:
    """比赛列表：主客队名称、联赛、开球时间、竞彩赔率数量。"""
    pool = get_pool()
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT m.id, m.competition, m.sporttery_no, m.kickoff_at, m.status,
                   th.canonical_name, ta.canonical_name,
                   (SELECT count(*) FROM odds_sporttery o WHERE o.match_id = m.id)
            FROM matches m
            JOIN teams th ON th.id = m.home_team_id
            JOIN teams ta ON ta.id = m.away_team_id
            ORDER BY m.kickoff_at, m.sporttery_no
            """
        )
        rows = cur.fetchall()
    items = [
        {
            "match_id": str(r[0]),
            "competition": r[1],
            "sporttery_no": r[2],
            "kickoff_at": r[3].isoformat() if r[3] else None,
            "status": r[4],
            "home_team": r[5],
            "away_team": r[6],
            "sporttery_count": r[7],
        }
        for r in rows
    ]
    return {"total": len(items), "matches": items}


@app.get("/api/v1/matches/{match_id}")
def match_detail(match_id: str) -> dict:
    """比赛详情：真实竞彩赔率与已归档比赛快照的模块状态；不公开整份原始负载。"""
    import json as _json
    pool = get_pool()
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT m.id, m.competition, m.sporttery_no, m.kickoff_at,
                   th.canonical_name, ta.canonical_name
            FROM matches m
            JOIN teams th ON th.id = m.home_team_id
            JOIN teams ta ON ta.id = m.away_team_id
            WHERE m.id = %s
            """,
            (match_id,),
        )
        m = cur.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="match not found")
        cur = conn.execute(
            """
            SELECT market, home_odds, draw_odds, away_odds, observed_at
            FROM odds_sporttery WHERE match_id = %s ORDER BY observed_at
            """,
            (match_id,),
        )
        sporttery = [
            {
                "market": r[0], "home_odds": str(r[1]), "draw_odds": str(r[2]),
                "away_odds": str(r[3]),
                "observed_at": r[4].isoformat() if r[4] else None,
            }
            for r in cur.fetchall()
        ]
        cur = conn.execute(
            """
            SELECT id, collected_at, raw_payload
            FROM match_snapshots WHERE match_id = %s AND data_status <> 'quarantined' ORDER BY collected_at
            """,
            (match_id,),
        )
        snapshots = []
        for r in cur.fetchall():
            payload = r[2]
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    payload = {"raw_text": payload}
            snapshots.append(
                {
                    "snapshot_id": str(r[0]),
                    "collected_at": r[1].isoformat() if r[1] else None,
                    "raw_payload": payload,
                }
            )
    # 六模块数据提取：兼容 titan007（中文顶层键）与小店火（details 子结构）
    raw = snapshots[-1]["raw_payload"] if snapshots else {}
    if not isinstance(raw, dict):
        raw = {}
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    modules = {
        "analysis": raw.get("分析") or details.get("match_base"),
        "asia": raw.get("亚让") or details.get("asia_stats"),
        "total_goals": raw.get("总进球") or details.get("total_goals"),
        "wdl": raw.get("胜平负") or details.get("europe_stats"),
        "corners": raw.get("角球") or details.get("corners"),
        "live_analysis": raw.get("现场分析") or details.get("live_analysis"),
    }
    # Source-level successful markers alone are not real business data.
    # The public view does not surface whole collector tables, which may
    # have malformed flattened columns or unverified corner semantics.
    for key, value in tuple(modules.items()):
        if isinstance(value, dict) and value.get("status") == "success" and not any(
            k not in {"status", "reason", "capturedAt", "collected_at", "source"}
            for k in value
        ):
            modules[key] = {"status": "not_collected", "reason": "no actual module fields"}
    # 新版采集器快照：附加七模块真实状态区块；旧来源比赛不含该字段，结构不变
    collector_v2 = None
    for s in reversed(snapshots):
        cv = collector_v2_modules(s["raw_payload"])
        if cv is not None:
            collector_v2 = cv
            break
    return {
        "match_id": str(m[0]),
        "competition": m[1],
        "sporttery_no": m[2],
        "kickoff_at": m[3].isoformat() if m[3] else None,
        "home_team": m[4],
        "away_team": m[5],
        "sporttery_odds": sporttery,
        "snapshots": [{"snapshot_id": s["snapshot_id"], "collected_at": s["collected_at"]}
                      for s in snapshots],
        "modules": modules,
        "collector_v2": collector_v2,
        "data_coverage": coverage_report({"modules": modules, "sporttery_odds": sporttery, "kickoff_at": m[3]}),
    }


@app.get("/")
def index_page():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "static", "index.html")
    )

# ---------- 外部 AI 专用比赛数据接口（纯数据，不预测） ----------
from .services.ai_match_service import get_ai_match_data

@app.get("/api/v1/ai/match/{match_id}")
def ai_match_data(match_id: str) -> dict:
    """外部 AI 读取比赛完整真实数据的专用接口。
    仅聚合数据库中的真实数据，不做任何预测或加工；缺失数据在 missing 中标记。"""
    return get_ai_match_data(match_id)
