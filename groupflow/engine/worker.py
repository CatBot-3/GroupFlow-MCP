"""Background worker: runs any queued searches for alternatives, sends reminders and closes votes when their time comes, and runs check-ins
on quiet plans.

Runs inside the server process as an asyncio task (scope plan 7.4 allows API and
worker to share code with separate entry points). Solves run in a thread so the
event loop keeps serving MCP requests.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any

log = logging.getLogger("groupflow.worker")


class Worker:
    def __init__(self, service: Any, interval_s: float = 0.15, checkins: Any = None, reminders_every_s: float = 1.0, checkins_every_s: float = 60.0):
        self.service = service
        self.interval_s = interval_s
        self.checkins = checkins
        self.reminders_every_s = reminders_every_s
        self.checkins_every_s = checkins_every_s
        self._reminders_at = 0.0
        self._checkins_at = 0.0
        self._coordination_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        # A database is owned by one server/worker. Claims from the previous
        # process cannot still be executing after startup. Retry the persisted
        # input; run_job's version checks preserve any intervening edit.
        recovered = self.service.db.tx(lambda: self.service.db.run(
            "UPDATE repair_jobs SET status='queued', started_at=NULL WHERE status='running'"))
        if recovered:
            log.info("requeued %s interrupted repair job(s)", recovered)
        # The first check-in pass waits one interval: a server that just started has not been watching anything go quiet.
        self._checkins_at = time.monotonic()
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
        now = time.monotonic()
        if now - self._coordination_at >= 60 and hasattr(self.service, 'reassess_coordination'):
            self._coordination_at = now
            await asyncio.to_thread(self.service.reassess_coordination)
        if now - self._reminders_at >= self.reminders_every_s and hasattr(self.service, "deliver_due_reminders"):
            self._reminders_at = now
            await asyncio.to_thread(self.service.deliver_due_reminders)
            if hasattr(self.service, "close_due_polls"):
                await asyncio.to_thread(self.service.close_due_polls)
        if self.checkins is not None and now - self._checkins_at >= self.checkins_every_s:
            self._checkins_at = now
            await self.checkins.run_pass()
