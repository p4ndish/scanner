from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import func, String, cast, text
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, Match, ScanJob
from backend.app.schemas import MatchOut
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/matches", tags=["matches"])


def _vkey(user_id: int, scan_id=None) -> str:
    """Redis key for verification progress, scoped per import when scan_id given."""
    scope = f"scan_{scan_id}" if scan_id else "all"
    return f"verify:{user_id}:{scope}:progress"


def _split_list(val):
    """Parse a comma-separated filter value into a clean list. None/empty -> []."""
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip()]


@router.get("")
def list_matches(
    scan_id: Optional[int] = Query(None),
    provider: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    max_score: Optional[int] = Query(None),
    llm_mode: Optional[bool] = Query(None),
    verified_status: Optional[str] = Query(None),
    model_type: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    canary: Optional[str] = Query(None),
    math: Optional[str] = Query(None),
    consistency: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = db.query(Match).options(joinedload(Match.scan_job)).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if scan_id:
        q = q.filter(Match.scan_job_id == scan_id)
    prov = _split_list(provider)
    if prov:
        q = q.filter(Match.provider.in_(prov))
    svc = _split_list(service)
    if svc:
        q = q.filter(Match.service.in_(svc))
    mtypes = _split_list(model_type)
    if mtypes:
        q = q.filter(Match.model_type.in_(mtypes))
    if min_score is not None:
        q = q.filter(Match.score >= min_score)
    if max_score is not None:
        q = q.filter(Match.score <= max_score)
    if llm_mode is not None:
        q = q.filter(ScanJob.llm_mode == llm_mode)
    vstat = _split_list(verified_status)
    if vstat:
        q = q.filter(Match.verified_status.in_(vstat))
    if model and model.strip():
        model_lower = model.strip().lower()
        q = q.filter(
            func.lower(Match.details_json.cast(String)).like(f"%{model_lower}%")
        )
    if ip and ip.strip():
        ip_val = ip.strip()
        if ':' in ip_val:
            ip_addr, _, ip_port = ip_val.partition(':')
            try:
                q = q.filter(Match.ip == ip_addr, Match.port == int(ip_port))
            except ValueError:
                q = q.filter(Match.ip == ip_addr)
        else:
            q = q.filter(Match.ip == ip_val)
    if canary and canary in ("pass", "fail"):
        expected = "true" if canary == "pass" else "false"
        q = q.filter(cast(Match.verification_details["canary_pass"], String) == expected)
    if math and math in ("pass", "fail"):
        expected = "true" if math == "pass" else "false"
        q = q.filter(cast(Match.verification_details["math_pass"], String) == expected)
    if consistency and consistency in ("pass", "fail"):
        expected = "true" if consistency == "pass" else "false"
        q = q.filter(cast(Match.verification_details["consistency_pass"], String) == expected)

    total = q.count()
    items = (
        q.order_by(Match.score.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "items": [MatchOut.model_validate(m) for m in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/stats")
def match_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    import os, json, time, redis as _redis

    cache_key = f"stats:{current_user.id}"
    lock_key = f"stats:{current_user.id}:lock"
    r = _redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    if not r.set(lock_key, "1", nx=True, ex=40):
        for _ in range(5):
            time.sleep(1)
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)
        return {"by_provider": [], "by_mode": {"opencode": 0, "llm": 0}, "by_verified": {}}

    from sqlalchemy import func

    try:
        db.execute(text("SET LOCAL statement_timeout = '30s'"))

        provider_q = (
            db.query(Match.provider, func.count(Match.id).label("count"))
            .join(ScanJob)
            .filter(ScanJob.user_id == current_user.id)
            .group_by(Match.provider)
            .all()
        )

        mode_q = (
            db.query(ScanJob.llm_mode, func.count(Match.id).label("count"))
            .join(ScanJob)
            .filter(ScanJob.user_id == current_user.id)
            .group_by(ScanJob.llm_mode)
            .all()
        )

        verify_q = (
            db.query(Match.verified_status, func.count(Match.id).label("count"))
            .join(ScanJob)
            .filter(ScanJob.user_id == current_user.id)
            .group_by(Match.verified_status)
            .all()
        )

        result = {
            "by_provider": [{"provider": p or "unknown", "count": c} for p, c in provider_q],
            "by_mode": {
                "opencode": sum(c for mode, c in mode_q if not mode),
                "llm": sum(c for mode, c in mode_q if mode),
            },
            "by_verified": {status: c for status, c in verify_q},
        }

        r.setex(cache_key, 60, json.dumps(result))
        return result
    except Exception:
        db.rollback()
        approx = db.execute(text(
            "SELECT verified_status, count(*) FROM matches GROUP BY verified_status"
        )).fetchall()
        result = {
            "by_provider": [],
            "by_mode": {"opencode": 0, "llm": 0},
            "by_verified": {row[0] or "pending": row[1] for row in approx},
        }
        r.setex(cache_key, 10, json.dumps(result))
        return result
    finally:
        r.delete(lock_key)


@router.get("/providers")
def list_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return distinct cloud providers for the current user's matches."""
    rows = (
        db.query(Match.provider)
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .distinct()
        .order_by(Match.provider)
        .all()
    )
    providers = [p or "unknown" for (p,) in rows]
    return {"providers": providers}


@router.get("/imports")
def list_imports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List all CLI-imported result batches for the current user.

    Each batch is a ScanJob whose providers contain 'cli_import'. Returns
    id, name, host count, mode, and import stats so the UI can render an
    Imports list page.
    """
    jobs = (
        db.query(ScanJob, func.count(Match.id).label("match_count"))
        .outerjoin(Match, Match.scan_job_id == ScanJob.id)
        .filter(
            ScanJob.user_id == current_user.id,
            cast(ScanJob.providers, String).like('%"cli_import"%'),
        )
        .group_by(ScanJob.id)
        .order_by(ScanJob.created_at.desc())
        .all()
    )

    out = []
    for job, match_count in jobs:
        stats = job.stats_json or {}
        out.append({
            "id": job.id,
            "name": job.name,
            "llm_mode": job.llm_mode,
            "match_count": match_count,
            "imported": stats.get("matches_found", match_count),
            "skipped": stats.get("duplicates_skipped", 0),
            "ports": job.ports or [],
            "created_at": job.created_at.isoformat() if job.created_at else None,
        })
    return {"imports": out, "total": len(out)}


@router.post("/import")
def import_cli_results(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Import results from a CLI scanner results.json file into the web database.

    Supports huge files (500MB+) via streaming parse + PostgreSQL COPY.
    An optional name can be provided to label this import batch.
    """
    import tempfile
    import os
    from backend.app.import_core import fast_import_results

    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Only JSON files are accepted")

    # Spool uploaded file to disk so we can mmap it
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.json') as tmp:
            while True:
                chunk = file.file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = tmp.name

        imported, skipped, job_id = fast_import_results(tmp_path, user_id=current_user.id, batch_size=50000, db_session=db, name=name)
        return {"imported": imported, "skipped": skipped, "scan_job_id": job_id}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)[:200]}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class VerifyPayload(BaseModel):
    provider: Optional[str] = None
    service: Optional[str] = None
    scan_id: Optional[int] = None
    verified_status: Optional[str] = None  # e.g. "pending" or "unreachable"
    use_proxy: bool = False


@router.post("/verify")
def start_verification(
    payload: VerifyPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Queue a background verification task for matches matching the filters."""
    from backend.app.tasks import verify_matches_task
    import redis
    import json
    import os

    # Count how many will be verified
    q = db.query(Match).join(ScanJob).filter(
        ScanJob.user_id == current_user.id,
        Match.verified_status.in_(["pending", "unreachable"]),
    )
    if payload.provider:
        q = q.filter(Match.provider.in_(_split_list(payload.provider)))
    if payload.service:
        q = q.filter(Match.service.in_(_split_list(payload.service)))
    if payload.scan_id:
        q = q.filter(Match.scan_job_id == payload.scan_id)
    if payload.verified_status:
        q = q.filter(Match.verified_status.in_(_split_list(payload.verified_status)))

    total = q.count()
    if total == 0:
        raise HTTPException(status_code=400, detail="No matches to verify")

    # Set initial progress in Redis
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.set(
        _vkey(current_user.id, payload.scan_id),
        json.dumps({
            "total": total,
            "done": 0,
            "legitimate": 0,
            "honeypot": 0,
            "unreachable": 0,
            "state": "queued",
        }),
        ex=3600,
    )

    # Queue Celery task
    task = verify_matches_task.delay(
        user_id=current_user.id,
        filters={
            "provider": payload.provider,
            "service": payload.service,
            "scan_id": payload.scan_id,
            "verified_status": payload.verified_status,
        },
        use_proxy=payload.use_proxy,
    )

    return {"queued": True, "total": total, "task_id": task.id}


@router.post("/verify/cancel")
def cancel_verification(
    current_user: User = Depends(get_current_active_user),
):
    """Stop the currently-running verification (any scope) for this user.

    Sets a Redis flag that the verify task checks between chunks; it exits
    cleanly after finishing the current chunk. State becomes "cancelled".
    """
    import redis
    import os
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.setex(f"verify:{current_user.id}:cancel", 3600, "1")
    return {"ok": True, "message": "Cancellation requested — verify will stop after the current chunk"}


@router.get("/verification-status")
def verification_status(
    scan_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_active_user),
):
    """Poll verification progress.

    If scan_id is given, returns that import's scoped progress under `progress`
    plus any global verify progress under `global_progress` (so an import page
    can note when a global verify is also running). Without scan_id, returns
    the global progress under `progress`.
    """
    import redis
    import json
    import os

    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    uid = current_user.id

    def _read(key):
        raw = r.get(key)
        return json.loads(raw) if raw else {"state": "idle", "total": 0, "done": 0}

    if scan_id:
        scoped = _read(f"verify:{uid}:scan_{scan_id}:progress")
        glob = _read(f"verify:{uid}:all:progress")
        scoped["scope"] = "scan"
        return {
            "progress": scoped,
            "global_progress": glob,
        }

    return _read(f"verify:{uid}:all:progress")


class ReverifyPayload(BaseModel):
    match_ids: Optional[List[int]] = None
    all_unreachable: bool = False
    scan_id: Optional[int] = None
    use_proxy: bool = False


@router.post("/reverify")
def reverify_matches(
    payload: ReverifyPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Re-verify specific matches or all unreachable matches."""
    from backend.app.tasks import verify_matches_task
    import redis
    import json
    import os

    from sqlalchemy import update

    # Build subquery of match IDs to re-verify
    q = db.query(Match.id).join(ScanJob).filter(
        ScanJob.user_id == current_user.id,
    )

    if payload.scan_id:
        q = q.filter(Match.scan_job_id == payload.scan_id)
    if payload.all_unreachable:
        q = q.filter(Match.verified_status == "unreachable")
    elif payload.match_ids:
        q = q.filter(Match.id.in_(payload.match_ids))
    else:
        raise HTTPException(status_code=400, detail="Provide match_ids or all_unreachable=true")

    # Capture the exact IDs before resetting (we verify them by ID afterwards).
    match_ids = [row[0] for row in q.all()]
    total = len(match_ids)
    if total == 0:
        raise HTTPException(status_code=400, detail="No matches to re-verify")

    # Reset status to pending for these matches
    stmt = update(Match).where(Match.id.in_(match_ids)).values(
        verified_status="pending", verified_at=None, verification_details={},
    )
    db.execute(stmt)
    db.commit()

    # Set progress in Redis
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.set(
        _vkey(current_user.id, payload.scan_id),
        json.dumps({
            "total": total,
            "done": 0,
            "legitimate": 0,
            "honeypot": 0,
            "unreachable": 0,
            "state": "queued",
        }),
        ex=3600,
    )

    task = verify_matches_task.delay(
        user_id=current_user.id,
        filters={"match_ids": match_ids, "scan_id": payload.scan_id},
        use_proxy=payload.use_proxy,
    )

    return {"queued": True, "total": total, "task_id": task.id}


class ReverifyAllPayload(BaseModel):
    use_proxy: bool = False


@router.post("/reverify-all")
def reverify_all_matches(
    payload: ReverifyAllPayload = ReverifyAllPayload(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Reset ALL verified matches back to pending and re-run verification."""
    from backend.app.tasks import verify_matches_task
    from sqlalchemy import update
    import redis
    import json
    import os

    # Count all matches for this user
    total = (
        db.query(Match.id)
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .count()
    )
    if total == 0:
        raise HTTPException(status_code=400, detail="No matches to verify")

    # Reset everything to pending
    subq = (
        db.query(Match.id)
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .subquery()
    )
    stmt = update(Match).where(Match.id.in_(subq)).values(
        verified_status="pending",
        verified_at=None,
        verification_details={},
    )
    db.execute(stmt)
    db.commit()

    # Set progress in Redis
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.set(
        _vkey(current_user.id, payload.scan_id),
        json.dumps({
            "total": total,
            "done": 0,
            "legitimate": 0,
            "honeypot": 0,
            "unreachable": 0,
            "state": "queued",
        }),
        ex=7200,
    )

    task = verify_matches_task.delay(user_id=current_user.id, filters={}, use_proxy=payload.use_proxy)

    return {"queued": True, "total": total, "task_id": task.id}


class ReverifyFilteredPayload(BaseModel):
    provider: Optional[str] = None
    service: Optional[str] = None
    scan_id: Optional[int] = None
    verified_status: Optional[str] = None
    canary: Optional[str] = None
    math: Optional[str] = None
    consistency: Optional[str] = None
    use_proxy: bool = False


@router.post("/reverify-filtered")
def reverify_filtered(
    payload: ReverifyFilteredPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Re-verify matches matching specific check result filters."""
    from backend.app.tasks import verify_matches_task
    from sqlalchemy import update
    import redis
    import json
    import os

    q = db.query(Match.id).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if payload.scan_id:
        q = q.filter(Match.scan_job_id == payload.scan_id)
    if payload.provider:
        q = q.filter(Match.provider.in_(_split_list(payload.provider)))
    if payload.service:
        q = q.filter(Match.service.in_(_split_list(payload.service)))
    if payload.verified_status:
        q = q.filter(Match.verified_status.in_(_split_list(payload.verified_status)))
    if payload.canary and payload.canary in ("pass", "fail"):
        expected = "true" if payload.canary == "pass" else "false"
        q = q.filter(cast(Match.verification_details["canary_pass"], String) == expected)
    if payload.math and payload.math in ("pass", "fail"):
        expected = "true" if payload.math == "pass" else "false"
        q = q.filter(cast(Match.verification_details["math_pass"], String) == expected)
    if payload.consistency and payload.consistency in ("pass", "fail"):
        expected = "true" if payload.consistency == "pass" else "false"
        q = q.filter(cast(Match.verification_details["consistency_pass"], String) == expected)

    # Capture the exact IDs that matched, BEFORE resetting them (their status /
    # details are about to change, so we can't re-select them by the same filter).
    match_ids = [row[0] for row in q.all()]
    total = len(match_ids)
    if total == 0:
        raise HTTPException(status_code=400, detail="No matches matching these filters")

    stmt = update(Match).where(Match.id.in_(match_ids)).values(
        verified_status="pending",
        verified_at=None,
        verification_details={},
    )
    db.execute(stmt)
    db.commit()

    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.set(
        _vkey(current_user.id, getattr(payload, "scan_id", None)),
        json.dumps({
            "total": total, "done": 0, "legitimate": 0,
            "honeypot": 0, "unreachable": 0, "state": "queued",
        }),
        ex=7200,
    )

    # Verify the exact reset set by ID (the old status/check filters no longer
    # apply — those rows are now 'pending' with cleared details).
    task = verify_matches_task.delay(
        user_id=current_user.id,
        filters={"match_ids": match_ids, "scan_id": payload.scan_id},
        use_proxy=payload.use_proxy,
    )

    return {"queued": True, "total": total, "task_id": task.id}


@router.get("/export")
def export_matches(
    provider: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    scan_id: Optional[int] = Query(None),
    verified_status: Optional[str] = Query(None),
    model_type: Optional[str] = Query(None),
    canary: Optional[str] = Query(None),
    math: Optional[str] = Query(None),
    consistency: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Export filtered matches as JSON with ip, ip:port, models format."""
    q = db.query(Match).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if scan_id:
        q = q.filter(Match.scan_job_id == scan_id)
    prov = _split_list(provider)
    if prov:
        q = q.filter(Match.provider.in_(prov))
    svc = _split_list(service)
    if svc:
        q = q.filter(Match.service.in_(svc))
    mtypes = _split_list(model_type)
    if mtypes:
        q = q.filter(Match.model_type.in_(mtypes))
    vstat = _split_list(verified_status)
    if vstat:
        q = q.filter(Match.verified_status.in_(vstat))
    if ip and ip.strip():
        ip_val = ip.strip()
        if ':' in ip_val:
            ip_addr, _, ip_port = ip_val.partition(':')
            try:
                q = q.filter(Match.ip == ip_addr, Match.port == int(ip_port))
            except ValueError:
                q = q.filter(Match.ip == ip_addr)
        else:
            q = q.filter(Match.ip == ip_val)
    if canary and canary in ("pass", "fail"):
        expected = "true" if canary == "pass" else "false"
        q = q.filter(cast(Match.verification_details["canary_pass"], String) == expected)
    if math and math in ("pass", "fail"):
        expected = "true" if math == "pass" else "false"
        q = q.filter(cast(Match.verification_details["math_pass"], String) == expected)
    if consistency and consistency in ("pass", "fail"):
        expected = "true" if consistency == "pass" else "false"
        q = q.filter(cast(Match.verification_details["consistency_pass"], String) == expected)

    matches = q.order_by(Match.score.desc()).all()

    result = []
    for m in matches:
        models = []

        vdetails = m.verification_details or {}
        if isinstance(vdetails, dict) and vdetails.get("models_found"):
            models = vdetails["models_found"]

        if not models:
            details = m.details_json or {}
            if isinstance(details, dict):
                if details.get("openai_model_id"):
                    models = details["openai_model_id"]
                elif details.get("ollama_tags"):
                    models = []

        result.append({
            "ip": m.ip,
            "ip:port": f"{m.ip}:{m.port}",
            "port": m.port,
            "scheme": m.scheme,
            "service": m.service,
            "provider": m.provider,
            "region": m.region,
            "score": m.score,
            "model_type": m.model_type,
            "verified_status": m.verified_status,
            "models": models,
        })

    return {"count": len(result), "matches": result}


class BulkDeletePayload(BaseModel):
    match_ids: Optional[List[int]] = None
    provider: Optional[str] = None
    service: Optional[str] = None
    verified_status: Optional[str] = None
    scan_id: Optional[int] = None


@router.delete("/bulk")
def bulk_delete_matches(
    payload: BulkDeletePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete matches by IDs or by filters."""
    from sqlalchemy import delete

    if payload.match_ids:
        # Delete specific IDs (must belong to user)
        subq = (
            db.query(Match.id)
            .join(ScanJob)
            .filter(ScanJob.user_id == current_user.id, Match.id.in_(payload.match_ids))
            .subquery()
        )
        stmt = delete(Match).where(Match.id.in_(subq))
        result = db.execute(stmt)
    else:
        # Delete by filters — build subquery first since SQLAlchemy
        # doesn't allow delete() directly on a query with join()
        q = db.query(Match.id).join(ScanJob).filter(ScanJob.user_id == current_user.id)
        if payload.provider:
            q = q.filter(Match.provider.in_(_split_list(payload.provider)))
        if payload.service:
            q = q.filter(Match.service.in_(_split_list(payload.service)))
        if payload.verified_status:
            q = q.filter(Match.verified_status.in_(_split_list(payload.verified_status)))
        if payload.scan_id:
            q = q.filter(Match.scan_job_id == payload.scan_id)
        subq = q.subquery()
        stmt = delete(Match).where(Match.id.in_(subq))
        result = db.execute(stmt)

    db.commit()
    return {"deleted": result.rowcount}


@router.get("/{match_id}/models")
def list_models_for_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Probe the LLM endpoint to discover available models."""
    from backend.app.llm_probe import probe_models

    match = (
        db.query(Match)
        .join(ScanJob)
        .filter(Match.id == match_id, ScanJob.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    base_url = f"{match.scheme}://{match.ip}:{match.port}"
    service = match.service or "unknown"

    try:
        models = probe_models(base_url, timeout=5)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach endpoint: {e}")

    return {"models": models, "service": service, "url": base_url}


class TestPromptPayload(BaseModel):
    model: str
    prompt: str = "hi"
    max_tokens: int = 100


@router.post("/{match_id}/test")
def test_prompt(
    match_id: int,
    payload: TestPromptPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send a test prompt to the LLM endpoint and return the response."""
    import requests
    from backend.app.llm_probe import probe_with_model

    match = (
        db.query(Match)
        .join(ScanJob)
        .filter(Match.id == match_id, ScanJob.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    base_url = f"{match.scheme}://{match.ip}:{match.port}"
    service = match.service or "unknown"
    model = payload.model
    prompt = payload.prompt

    # ── Try 1: OpenAI-compatible chat completions (most universal) ──
    try:
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model or "",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": payload.max_tokens,
                "stream": False,
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            return {
                "response": msg.get("content", ""),
                "finish_reason": choice.get("finish_reason"),
                "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
                "completion_tokens": data.get("usage", {}).get("completion_tokens"),
                "endpoint": "/v1/chat/completions",
            }
    except Exception:
        pass

    # ── Try 2: Ollama /api/generate ──
    if service in ("ollama",):
        try:
            r = requests.post(
                f"{base_url}/api/generate",
                json={"model": model or "", "prompt": prompt, "stream": False},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "response": data.get("response", ""),
                    "done": data.get("done", True),
                    "total_duration_ms": data.get("total_duration", 0) / 1e6,
                    "prompt_eval_count": data.get("prompt_eval_count", 0),
                    "eval_count": data.get("eval_count", 0),
                    "endpoint": "/api/generate",
                }
        except Exception:
            pass

    # ── Try 3: Ollama /api/chat ──
    if service in ("ollama",):
        try:
            r = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model or "",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                msg = data.get("message", {})
                return {
                    "response": msg.get("content", "") if isinstance(msg, dict) else str(msg),
                    "done": data.get("done", True),
                    "endpoint": "/api/chat",
                }
        except Exception:
            pass

    # ── Try 4: Kobold /api/v1/generate ──
    try:
        r = requests.post(
            f"{base_url}/api/v1/generate",
            json={"prompt": prompt, "max_length": payload.max_tokens},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [{}])
            return {"response": results[0].get("text", ""), "endpoint": "/api/v1/generate"}
    except Exception:
        pass

    # ── Try 5: OpenAI legacy /v1/completions ──
    try:
        r = requests.post(
            f"{base_url}/v1/completions",
            json={"model": model or "", "prompt": prompt, "max_tokens": payload.max_tokens},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            choices = data.get("choices", [{}])
            return {
                "response": choices[0].get("text", ""),
                "endpoint": "/v1/completions",
            }
    except Exception:
        pass

    # ── Final fallback: use shared probe (shouldn't reach here normally) ──
    resp = probe_with_model(base_url, prompt, model, timeout=15)
    if resp is not None:
        return {"response": resp, "endpoint": "fallback"}

    raise HTTPException(status_code=502, detail="All endpoints returned errors or were unreachable")
