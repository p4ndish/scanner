from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, Match, ScanJob
from backend.app.schemas import MatchOut
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=List[MatchOut])
def list_matches(
    scan_id: Optional[int] = Query(None),
    provider: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    llm_mode: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = db.query(Match).options(joinedload(Match.scan_job)).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if scan_id:
        q = q.filter(Match.scan_job_id == scan_id)
    if provider:
        q = q.filter(Match.provider == provider)
    if service:
        q = q.filter(Match.service == service)
    if min_score is not None:
        q = q.filter(Match.score >= min_score)
    if llm_mode is not None:
        q = q.filter(ScanJob.llm_mode == llm_mode)

    return q.order_by(Match.score.desc()).limit(1000).all()


@router.get("/stats")
def match_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from sqlalchemy import func

    # Provider breakdown
    provider_q = (
        db.query(Match.provider, func.count(Match.id).label("count"))
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(Match.provider)
        .all()
    )

    # Mode breakdown (opencode vs LLM)
    mode_q = (
        db.query(ScanJob.llm_mode, func.count(Match.id).label("count"))
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(ScanJob.llm_mode)
        .all()
    )

    return {
        "by_provider": [{"provider": p or "unknown", "count": c} for p, c in provider_q],
        "by_mode": {
            "opencode": sum(c for mode, c in mode_q if not mode),
            "llm": sum(c for mode, c in mode_q if mode),
        },
    }


@router.post("/import")
def import_cli_results(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Import results from a CLI scanner results.json file into the web database."""
    import json
    from datetime import datetime
    from backend.app.models import ScanJob, Match

    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Only JSON files are accepted")

    try:
        content = file.file.read()
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    matches = data.get("matches", [])
    if not matches:
        raise HTTPException(status_code=400, detail="No matches found in uploaded file")

    # Determine if LLM mode from file contents
    is_llm = any(m.get("service") in ("ollama", "vllm", "llamacpp", "kobold", "textgen") for m in matches)

    # Create a synthetic scan job
    job = ScanJob(
        user_id=current_user.id,
        name=f"CLI Import {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        status="completed",
        providers=["cli_import"],
        ports=list(set(str(m.get("port", 0)) for m in matches)),
        llm_mode=is_llm,
        score_threshold=5,
        stats_json=data.get("stats", {}),
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    imported = 0
    for m in matches:
        match = Match(
            scan_job_id=job.id,
            ip=m.get("ip", "unknown"),
            port=m.get("port", 0),
            scheme=m.get("scheme", "http"),
            score=m.get("score", 0),
            service=m.get("service", "unknown"),
            provider=m.get("provider"),
            region=m.get("region"),
            methods_hit=m.get("methods_hit", []),
            details_json=m.get("details", {}),
        )
        db.add(match)
        imported += 1

    db.commit()
    return {"imported": imported, "scan_job_id": job.id}
