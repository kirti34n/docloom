"""In-process async jobs with SSE event streams.

# ponytail: jobs die on restart; acceptable for minutes-long local work —
# persisted queue only if that ever hurts.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from .db import execute, new_id, now, query_one

_SENTINEL = {"stage": "__end__"}


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    queues: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None


JOBS: dict[str, Job] = {}


class JobCtx:
    def __init__(self, job: Job):
        self._job = job

    @property
    def job_id(self) -> str:
        return self._job.id

    def emit(self, stage: str, status: str = "running",
             detail: str = "", data: Any = None) -> None:
        event = {"stage": stage, "status": status, "detail": detail,
                 "data": data, "t": now()}
        self._job.events.append(event)
        execute("UPDATE jobs SET events_json = ?, updated = ? WHERE id = ?",
                (json.dumps(self._job.events), now(), self._job.id))
        for q in list(self._job.queues):
            q.put_nowait(event)


def start_job(
    kind: str,
    work: Callable[[JobCtx], Awaitable[None]],
    notebook_id: str | None = None,
    artifact_id: str | None = None,
) -> str:
    job = Job(id=new_id(), kind=kind)
    JOBS[job.id] = job
    execute(
        "INSERT INTO jobs (id, kind, status, notebook_id, artifact_id, "
        "events_json, created, updated) VALUES (?, ?, 'running', ?, ?, '[]', ?, ?)",
        (job.id, kind, notebook_id, artifact_id, now(), now()),
    )
    ctx = JobCtx(job)

    async def runner() -> None:
        try:
            await work(ctx)
            job.status = "done"
            ctx.emit("job", "done")
        except asyncio.CancelledError:
            job.status = "cancelled"
            ctx.emit("job", "cancelled")
        except Exception as e:
            job.status = "failed"
            ctx.emit("job", "failed", detail=f"{type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            execute("UPDATE jobs SET status = ?, updated = ? WHERE id = ?",
                    (job.status, now(), job.id))
            for q in list(job.queues):
                q.put_nowait(_SENTINEL)

    job.task = asyncio.get_event_loop().create_task(runner())
    return job.id


def cancel_job(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if job and job.task and not job.task.done():
        job.task.cancel()
        return True
    return False


def job_state(job_id: str) -> dict[str, Any] | None:
    job = JOBS.get(job_id)
    if job is not None:
        return {"id": job.id, "kind": job.kind, "status": job.status,
                "events": job.events}
    row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if row is None:
        return None
    return {"id": row["id"], "kind": row["kind"], "status": row["status"],
            "events": json.loads(row["events_json"])}


async def sse_events(job_id: str) -> AsyncIterator[str]:
    """Replay stored events, then stream live ones, as SSE frames."""

    def frame(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event)}\n\n"

    job = JOBS.get(job_id)
    if job is None:
        state = job_state(job_id)
        if state is None:
            yield frame({"stage": "job", "status": "unknown"})
            return
        for event in state["events"]:
            yield frame(event)
        return

    queue: asyncio.Queue = asyncio.Queue()
    job.queues.append(queue)
    try:
        for event in list(job.events):
            yield frame(event)
        if job.status != "running":
            return
        while True:
            event = await queue.get()
            if event is _SENTINEL:
                return
            yield frame(event)
    finally:
        if queue in job.queues:
            job.queues.remove(queue)
