"""worker.py — in-process asyncio job queue, concurrency=1 (plan §5, B2/B3).

Single consumer loop. Each job is wrapped in its own try/except so one job's
failure (LLM down, render crash, anything) marks that generation 'error' and the
loop keeps serving the queue. Client sees a friendly message; the real traceback
goes to the log.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import db
import pipeline

logger = logging.getLogger("carousel.worker")


class Worker:
    def __init__(self, segmenter: Optional[Callable] = None, renderer: Optional[Callable] = None):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._segmenter = segmenter
        self._renderer = renderer
        self._task: Optional[asyncio.Task] = None

    async def enqueue(self, job: dict) -> None:
        await self.queue.put(job)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            job = await self.queue.get()
            job_id = job.get("job_id", "?")
            try:
                await pipeline.process_job(job, self._segmenter, self._renderer)
            except pipeline.PipelineError as e:
                logger.error("job %s failed: %s", job_id, e.client_message)
                await self._fail(job_id, e.client_message)
            except Exception:
                logger.exception("job %s crashed unexpectedly", job_id)
                await self._fail(job_id, pipeline.GENERIC_ERROR)
            finally:
                self.queue.task_done()

    @staticmethod
    async def _fail(job_id: str, message: str) -> None:
        try:
            await db.update_generation(job_id, status="error", error=message)
        except Exception:
            logger.exception("could not mark job %s as error", job_id)
