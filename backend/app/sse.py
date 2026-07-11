import asyncio
import json

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models import User, ScanJob

router = APIRouter()
settings = get_settings()


def _get_user_from_token(token: str, db: Session):
    from jose import jwt, JWTError
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return db.query(User).filter(User.id == int(user_id)).first()
    except JWTError:
        return None


async def event_generator(scan_id: int):
    r = redis.from_url(settings.REDIS_URL)
    pubsub = r.pubsub()
    pubsub.subscribe(f"scan:{scan_id}:events")

    # Send any cached state immediately
    cached = r.get(f"scan:{scan_id}:state")
    if cached:
        yield f"data: {cached.decode()}\n\n"

    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                payload = message["data"].decode()
                yield f"data: {payload}\n\n"
            await asyncio.sleep(0.1)
    finally:
        pubsub.unsubscribe()
        pubsub.close()


@router.get("/{scan_id}/events")
async def scan_events(
    scan_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    user = _get_user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    job = db.query(ScanJob).filter(ScanJob.id == scan_id, ScanJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Scan not found")

    return StreamingResponse(
        event_generator(scan_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
