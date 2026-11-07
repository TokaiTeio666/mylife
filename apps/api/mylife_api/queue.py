"""Celery 任务投递（API 不导入 worker 代码，保持依赖方向）。"""

from __future__ import annotations

import logging

from celery import Celery
from mylife_contract.errors import ErrorCode

from mylife_api.errors import AppError
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.queue")

_client: Celery | None = None


def _get_client() -> Celery:
    global _client
    if _client is None:
        _client = Celery("mylife-api-client", broker=settings.redis_url)
    return _client


def enqueue_ingest(kind: str, job_id: str, workspace_id: str) -> None:
    """投递导入任务；`MYLIFE_QUEUE_BACKEND=off` 时仅记录（测试用）。"""
    if settings.queue_backend == "off":
        logger.info("queue_disabled job=%s kind=%s", job_id, kind)
        return
    task = f"mylife.ingest_{kind}"
    try:
        _get_client().send_task(task, args=[job_id, workspace_id], queue="q.ingest")
    except Exception as exc:  # noqa: BLE001 - broker 不可用时显式失败
        logger.error("enqueue_failed task=%s error=%s", task, type(exc).__name__)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE) from exc
