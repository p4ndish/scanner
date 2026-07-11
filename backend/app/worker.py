import json
import os
from celery import Celery
from celery.signals import worker_ready

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
