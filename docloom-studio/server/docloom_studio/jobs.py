"""In-process async jobs with SSE event streams.

Jobs run in the event loop of the process that started them, so a restart
loses the running task. `reconcile_jobs()` (called at startup) marks any job
still flagged 'running' in the DB as 'failed' so the UI never hangs forever
polling a zombie that no worker will ever finish.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from .db import execute, new_id, now, query_all, query_one

_SENTINEL = {"stage": "__end__"}
log = logging.getLogger("docloom_studio.jobs")

# Bound concurrent job bodies so N simultaneous uploads/generations don't
# stampede the model provider and the DB. Tune via env; work queues past it.
_MAX_CONCURRENT = max(1, int(os.environ.get("DOCLOOM_MAX_CONCURRENT_JOBS", "4")))
_sem: asyncio.Semaphore | None = None

# SSE idle heartbeat: a single slide/section call can be silent for minutes
# (providers.TIMEOUT=600s) with nothing sent over the wire, which is longer
# than most proxies'/load balancers' idle timeout and drops the connection,
# freezing the build UI. A `:` comment line is valid SSE (EventSource ignores
# it) that just keeps the connection alive.
_HEARTBEAT_SECONDS = 15.0

# Cap in-memory retention of finished jobs so full per-slide payloads don't
# leak for the server's lifetime; job_events (the DB) is the durable record
# job_state()/sse_events() fall back to once a job is no longer in JOBS.
_MAX_FINISHED_JOBS_KEPT = 200


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:  # created lazily so it binds to the running loop
        _sem = asyncio.Semaphore(_MAX_CONCURRENT)
    return _sem


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    seq: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    queues: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None


JOBS: dict[str, Job] = {}


def _append_event(job_id: str, seq: int, stage: str, status: str,
                  detail: str, data: Any) -> None:
    execute(
        "INSERT INTO job_events (job_id, seq, stage, status, detail, data_json, t) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, seq, stage, status, detail,
         json.dumps(data) if data is not None else None, now()),
    )


def _events_for(job_id: str) -> list[dict[str, Any]]:
    rows = query_all(
        "SELECT stage, status, detail, data_json, t FROM job_events "
        "WHERE job_id = ? ORDER BY seq", (job_id,))
    return [{"stage": r["stage"], "status": r["status"], "detail": r["detail"],
             "data": json.loads(r["data_json"]) if r["data_json"] else None,
             "t": r["t"]} for r in rows]


def reconcile_jobs() -> int:
    """Fail-close jobs orphaned by a restart.

    A job marked 'running' in the DB has no live task after the process that
    owned it exits (in-process queue, not persisted). At startup we can only
    have zombies, so mark every DB 'running' job 'failed' with a terminal
    event. Returns how many were reconciled. Idempotent.
    """
    rows = query_all("SELECT id FROM jobs WHERE status = 'running'")
    for row in rows:
        nxt = query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM job_events "
                        "WHERE job_id = ?", (row["id"],))["m"] + 1
        _append_event(row["id"], nxt, "job", "failed",
                      "interrupted by server restart", None)
        execute("UPDATE jobs SET status = 'failed', updated = ? WHERE id = ?",
                (now(), row["id"]))
    # Any artifact still 'building' at startup is orphaned (its job cannot be
    # live), so fail it too. Otherwise it spins forever in the UI and, being
    # non-terminal, can be neither opened nor deleted.
    execute("UPDATE artifacts SET status = 'failed' WHERE status = 'building'")
    if rows:
        log.warning("reconciled %d interrupted job(s) on startup", len(rows))
    return len(rows)


class JobCtx:
    def __init__(self, job: Job):
        self._job = job

    @property
    def job_id(self) -> str:
        return self._job.id

    def emit(self, stage: str, status: str = "running",
             detail: str = "", data: Any = None) -> None:
        self._job.seq += 1
        event = {"stage": stage, "status": status, "detail": detail,
                 "data": data, "t": now()}
        self._job.events.append(event)
        # append-only: one row per event, not a rewrite of the whole log
        _append_event(self._job.id, self._job.seq, stage, status, detail, data)
        for q in list(self._job.queues):
            q.put_nowait(event)


def _prune_finished_jobs() -> None:
    """Bound JOBS's lifetime memory. Only terminal jobs with no live SSE
    subscriber are candidates (an active `sse_events` reader needs the job to
    stay put); once there are more than _MAX_FINISHED_JOBS_KEPT of those, drop
    the oldest first (JOBS preserves insertion == creation order)."""
    finished = [j for j in JOBS.values() if j.status != "running" and not j.queues]
    excess = len(finished) - _MAX_FINISHED_JOBS_KEPT
    for job in finished[:max(0, excess)]:
        JOBS.pop(job.id, None)


def _fail_artifact(artifact_id: str) -> None:
    """Best-effort: mark the artifact a job was building as 'failed' so a
    broken stub doesn't sit forever in the artifacts list as 'building'.
    Never raises -- this runs from inside the job runner's own error handling
    and must not shadow the original failure."""
    try:
        from .artifacts import set_artifact_status

        set_artifact_status(artifact_id, "failed")
    except Exception:
        log.warning("could not mark artifact %s failed", artifact_id)


def start_job(
    kind: str,
    work: Callable[[JobCtx], Awaitable[None]],
    notebook_id: str | None = None,
    artifact_id: str | None = None,
) -> str:
    _prune_finished_jobs()
    job = Job(id=new_id(), kind=kind)
    JOBS[job.id] = job
    execute(
        "INSERT INTO jobs (id, kind, status, notebook_id, artifact_id, "
        "events_json, created, updated) VALUES (?, ?, 'running', ?, ?, '[]', ?, ?)",
        (job.id, kind, notebook_id, artifact_id, now(), now()),
    )
    ctx = JobCtx(job)
    log.info("job %s start kind=%s notebook=%s", job.id, kind, notebook_id)

    async def runner() -> None:
        try:
            async with _semaphore():   # bounded concurrency for the heavy body
                await work(ctx)
            job.status = "done"
            ctx.emit("job", "done")
            log.info("job %s done", job.id)
        except asyncio.CancelledError:
            job.status = "cancelled"
            ctx.emit("job", "cancelled")
            log.info("job %s cancelled", job.id)
            if artifact_id:
                _fail_artifact(artifact_id)
        except Exception as e:
            job.status = "failed"
            ctx.emit("job", "failed", detail=f"{type(e).__name__}: {e}")
            log.error("job %s failed: %s", job.id, e)
            traceback.print_exc()
            if artifact_id:
                _fail_artifact(artifact_id)
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
            "events": _events_for(job_id)}


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
    # Snapshot "was it running" at subscribe time, not after replay: the
    # replay loop below can suspend on send, letting the job finish mid
    # replay. If we re-check job.status after replay we might see a
    # terminal status and bail out without draining the queue, dropping
    # the events (including the terminal one) that arrived during replay.
    was_running = job.status == "running"
    try:
        for event in list(job.events):
            yield frame(event)
        if not was_running:
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"  # SSE comment: keeps the connection alive,
                continue                # ignored by EventSource, no event fires
            if event is _SENTINEL:
                return
            yield frame(event)
    finally:
        if queue in job.queues:
            job.queues.remove(queue)
