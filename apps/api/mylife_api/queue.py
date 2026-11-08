"""Celery 任务投递（API 不导入 worker 代码，保持依赖方向）。

任务只能在**事务提交后**投递：否则 worker 可能先于 commit 读到 job，
出现 "job not found" 的静默丢失（NFR-4 / FX-09）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from celery import Celery
from mylife_contract.errors import ErrorCode
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from mylife_api.errors import AppError
from mylife_api.settings import settings

logger = logging.getLogger("mylife.api.queue")

_client: Celery | None = None

#: 每个 session 的待投递任务与 after-commit 动作（按 id 隔离，session 生命周期短）。
_pending_jobs: dict[int, list[tuple[str, str, str]]] = {}
_pending_actions: dict[int, list[Callable[[], None]]] = {}
_hooked: set[int] = set()


def _get_client() -> Celery:
    global _client
    if _client is None:
        _client = Celery("mylife-api-client", broker=settings.redis_url)
    return _client


def _send(kind: str, job_id: str, workspace_id: str) -> None:
    if settings.queue_backend == "off":
        logger.info("queue_disabled job=%s kind=%s", job_id, kind)
        return
    task = f"mylife.ingest_{kind}"
    try:
        _get_client().send_task(task, args=[job_id, workspace_id], queue="q.ingest")
    except Exception as exc:  # noqa: BLE001 - broker 不可用时显式失败
        logger.error("enqueue_failed task=%s error=%s", task, type(exc).__name__)
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE) from exc


def _install_hooks(session: AsyncSession) -> None:
    key = id(session)
    if key in _hooked:
        return
    _hooked.add(key)

    def _after_commit(_sync: Any) -> None:
        for kind, job_id, workspace_id in _pending_jobs.pop(key, []):
            try:
                _send(kind, job_id, workspace_id)
            except AppError:
                logger.error("enqueue_after_commit_failed job=%s", job_id)
        for action in _pending_actions.pop(key, []):
            try:
                action()
            except Exception:  # noqa: BLE001 - 已提交事务不得因此回滚
                logger.exception("after_commit action failed")

    def _after_rollback(_sync: Any) -> None:
        _pending_jobs.pop(key, None)
        _pending_actions.pop(key, None)

    event.listen(session.sync_session, "after_commit", _after_commit)
    event.listen(session.sync_session, "after_rollback", _after_rollback)


def enqueue_ingest(
    kind: str,
    job_id: str,
    workspace_id: str,
    *,
    session: AsyncSession | None = None,
) -> None:
    """投递导入任务；给了 `session` 时延迟到 commit 之后。

    `MYLIFE_QUEUE_BACKEND=off` 时仅记录（测试/单机调试）。
    """
    if session is None:
        _send(kind, job_id, workspace_id)
        return
    _install_hooks(session)
    _pending_jobs.setdefault(id(session), []).append((kind, job_id, workspace_id))


def run_after_commit(session: AsyncSession, action: Callable[[], None]) -> None:
    """通用 after-commit 钩子（交付/通知等非 Celery 场景）。"""
    _install_hooks(session)
    _pending_actions.setdefault(id(session), []).append(action)
