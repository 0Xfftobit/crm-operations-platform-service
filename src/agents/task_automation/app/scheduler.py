"""Background scheduler for recurring tasks and overdue sweeps."""
from __future__ import annotations

import asyncio

import structlog

from app.core.config import get_settings
from app.service import mark_overdue_tasks, process_escalations, spawn_recurring_tasks

logger = structlog.get_logger(__name__)

_task: asyncio.Task | None = None


async def _loop() -> None:
    settings = get_settings()
    while True:
        try:
            overdue = mark_overdue_tasks()
            spawned = spawn_recurring_tasks()
            escalated = process_escalations()
            if overdue or spawned or escalated:
                logger.info(
                    "scheduler_tick",
                    overdue=overdue,
                    recurring_spawned=len(spawned),
                    sla_escalated=len(escalated),
                )
        except Exception:
            logger.exception("scheduler_tick_failed")
        await asyncio.sleep(settings.scheduler_interval_s)


async def start_scheduler() -> None:
    global _task
    settings = get_settings()
    if not settings.scheduler_enabled:
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        logger.info("scheduler_started", interval_s=settings.scheduler_interval_s)


async def stop_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
