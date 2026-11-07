"""Celery 应用与任务注册。"""

from __future__ import annotations

from celery import Celery

from mylife_worker.config import settings
from mylife_worker.jobs import run_job_sync

celery_app = Celery("mylife", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    task_default_queue="q.ingest",
    task_routes={
        "mylife.ingest_zip": {"queue": "q.ingest"},
        "mylife.ingest_git": {"queue": "q.ingest"},
    },
)


@celery_app.task(name="mylife.ingest_zip")
def ingest_zip_task(job_id: str, workspace_id: str) -> None:
    run_job_sync(job_id, workspace_id)


@celery_app.task(name="mylife.ingest_git")
def ingest_git_task(job_id: str, workspace_id: str) -> None:
    run_job_sync(job_id, workspace_id)
