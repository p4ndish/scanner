from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, ScanJob, Match, ScanLog
from backend.app.schemas import ScanJobCreate, ScanJobOut, ScanJobDetailOut, ScanJobList, ScanLogOut
from pydantic import BaseModel
from typing import Any
from backend.app.tasks import run_scan_task

router = APIRouter(prefix="/scans", tags=["scans"])


@router.post("", response_model=ScanJobOut)
@router.post("/", response_model=ScanJobOut, include_in_schema=False)
def create_scan(
    data: ScanJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Validate: need either providers or target_ip
    if not data.target_ip and (not data.providers or len(data.providers) == 0):
        raise HTTPException(status_code=422, detail="Select at least one provider or provide a target_ip")

    # Default ports
    default_ports = ["4096", "3000", "8080"] if not data.llm_mode else ["11434", "8080", "8000", "1234", "5000", "5001", "7860", "8888", "3001"]
    ports = data.ports or default_ports

    job = ScanJob(
        user_id=current_user.id,
        name=data.name,
        providers=data.providers or [],
        target_ip=data.target_ip,
        ports=ports,
        llm_mode=data.llm_mode,
        rate=data.rate,
        workers=data.workers,
        parallel=data.parallel,
        score_threshold=data.score_threshold,
        full_sweep=data.full_sweep,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Trigger Celery task
    run_scan_task.delay(job.id)

    return job


@router.get("", response_model=List[ScanJobList])
@router.get("/", response_model=List[ScanJobList], include_in_schema=False)
def list_scans(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    jobs = (
        db.query(ScanJob, func.count(Match.id).label("match_count"))
        .outerjoin(Match, Match.scan_job_id == ScanJob.id)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(ScanJob.id)
        .order_by(ScanJob.created_at.desc())
        .all()
    )

    out = []
    for job, match_count in jobs:
        row = ScanJobList.model_validate(job)
        row.match_count = match_count
        out.append(row)
    return out


@router.get("/{scan_id}", response_model=ScanJobDetailOut)
def get_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from sqlalchemy.orm import joinedload
    job = (
        db.query(ScanJob)
        .options(joinedload(ScanJob.matches))
        .filter(ScanJob.id == scan_id, ScanJob.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")
    return job


@router.delete("/{scan_id}")
def delete_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    job = db.query(ScanJob).filter(ScanJob.id == scan_id, ScanJob.user_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.get("/{scan_id}/logs", response_model=List[ScanLogOut])
def get_scan_logs(
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    job = db.query(ScanJob).filter(ScanJob.id == scan_id, ScanJob.user_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")
    return job.logs


@router.post("/{scan_id}/cancel", response_model=ScanJobOut)
def cancel_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    job = db.query(ScanJob).filter(ScanJob.id == scan_id, ScanJob.user_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")
    if job.status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel scan with status '{job.status}'")

    # Set cancellation flag in Redis so the worker sees it
    import redis
    from backend.app.config import get_settings
    r = redis.from_url(get_settings().REDIS_URL)
    r.setex(f"scan:{scan_id}:cancelled", 3600, "1")

    job.status = "cancelled"
    job.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job
