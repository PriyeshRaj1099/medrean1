"""Background job queue abstraction.

The default implementation runs jobs as asyncio tasks inside the API process,
which keeps development and single-server deployments dependency-free. The
interface is deliberately minimal (``enqueue`` + ``shutdown``) so a Celery / RQ /
Arq publisher backed by Redis can be dropped in for multi-worker production
deployments without touching any call site.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from functools import lru_cache

logger = logging.getLogger("medilens.jobs")

JobCallable = Callable[[], Awaitable[None]]


class JobQueue(ABC):
    """Minimal contract every queue implementation must satisfy."""

    @abstractmethod
    async def enqueue(self, name: str, job: JobCallable) -> None:
        """Schedule ``job`` for background execution."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Wait for in-flight jobs and release resources."""


class InProcessJobQueue(JobQueue):
    """asyncio-task based queue with a bounded worker count."""

    def __init__(self, max_concurrency: int = 3) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue(self, name: str, job: JobCallable) -> None:
        task = asyncio.create_task(self._run(name, job), name=f"medilens:{name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info(
            "job queued (queue=%d)", len(self._tasks), extra={"event": "job_queued", "job": name}
        )

    async def _run(self, name: str, job: JobCallable) -> None:
        async with self._semaphore:
            try:
                await job()
                logger.info("job finished", extra={"event": "job_finished", "job": name})
            except asyncio.CancelledError:
                logger.warning("job cancelled", extra={"event": "job_cancelled", "job": name})
                raise
            except Exception:
                # Never let a background failure crash the process; the job
                # itself is responsible for persisting user-visible state.
                logger.exception("job failed", extra={"event": "job_failed", "job": name})

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks if not task.done()]
        if not tasks:
            return
        logger.info(
            "waiting for %d background job(s)", len(tasks), extra={"event": "jobs_draining"}
        )
        await asyncio.gather(*tasks, return_exceptions=True)


@lru_cache
def get_job_queue() -> JobQueue:
    return InProcessJobQueue()
