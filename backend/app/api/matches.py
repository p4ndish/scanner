from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, Match, ScanJob
from backend.app.schemas import MatchOut

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=List[MatchOut])
def list_matches(
    scan_id: Optional[int] = Query(None),
    provider: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = db.query(Match).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if scan_id:
        q = q.filter(Match.scan_job_id == scan_id)
    if provider:
        q = q.filter(Match.provider == provider)
    if service:
        q = q.filter(Match.service == service)
    if min_score is not None:
        q = q.filter(Match.score >= min_score)

    return q.order_by(Match.score.desc()).limit(1000).all()


@router.get("/stats")
def match_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from sqlalchemy import func

    q = (
        db.query(Match.provider, func.count(Match.id).label("count"))
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(Match.provider)
        .all()
    )
    return [{"provider": p or "unknown", "count": c} for p, c in q]
