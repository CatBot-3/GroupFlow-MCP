"""Background worker: runs any queued searches for alternatives.

Runs inside the server process as an asyncio task (scope plan 7.4 allows API and
worker to share code with separate entry points). Solves run in a thread so the
event loop keeps serving MCP requests.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

log = logging.getLogger("groupflow.worker")


class Worker:
    def __init__(self, service: Any, interval_s: float = 0.15):
        self.service = service
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="groupflow-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover
                self._task.cancel()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except sqlite3.ProgrammingError as err:
                if "closed" in str(err):  # the database closed under us during shutdown: nothing left to do
                    return
                log.exception("worker tick failed")
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("worker tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        jobs = self.service.db.all("SELECT id FROM repair_jobs WHERE status = 'queued' ORDER BY created_at")
        for job in jobs:
            await asyncio.to_thread(self.service.run_job, job["id"])
