import json
import os
from celery import Celery
from celery.signals import worker_ready, worker_process_init

from backend.app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "scanner_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600 * 24,  # 24 hours max for a scan
    worker_prefetch_multiplier=1,
)


@worker_ready.connect
def on_worker_ready(**kwargs):
    print("Scanner worker ready. masscan/zmap should be available in this container.")


@worker_process_init.connect
def on_worker_process_init(**kwargs):
    """Dispose the SQLAlchemy connection pool in each forked worker process.

    Celery forks child processes that inherit the parent's DB connection pool.
    Sharing those connections across processes causes intermittent stale reads
    (e.g. a verify counting 0 rows when there are clearly pending matches).
    Disposing forces each fork to open its own fresh connections.
    """
    try:
        from backend.app.database import engine
        engine.dispose()
    except Exception as exc:
        print(f"engine.dispose() on fork failed: {exc}")
