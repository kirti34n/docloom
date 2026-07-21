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
import sqlite3
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from .db import IS_POSTGRES, execute, new_id, now, query_all, query_one

_SENTINEL = {"stage": "__end__"}
log = logging.getLogger("docloom_studio.jobs")

# Concurrent reapers on shared Postgres can both try to reclaim the same
# zombie job; the terminal event insert races on the (job_id, seq) PK, and
# only one writer should win.
_INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
if IS_POSTGRES:
    try:
        import psycopg
        _INTEGRITY_ERRORS += (psycopg.IntegrityError,)
    except ImportError:
        pass

# Bound concurrent job bodies so N simultaneous uploads/generations don't
# stampede the model provider and the DB. Tune via env; work queues past it.
_MAX_CONCURRENT = max(1, int(os.environ.get("DOCLOOM_MAX_CONCURRENT_JOBS", "4")))
_sem: asyncio.Semaphore | None = None

# SSE idle heartbeat: a single slide/section call can be silent for minutes
# (providers.TIMEOUT=600s) with nothing sent over the wire, which is longer
# than most proxies'/load balancers' idle timeout and drops the connection,
# freezing the build UI. A `:` comment line is valid SSE (EventSource ignores
# it) that just keeps the connection alive.
_SSE_KEEPALIVE_SECONDS = 15.0

# DB job-liveness heartbeat (distinct from the SSE keepalive above): how
# often a running job's row gets its `heartbeat` column refreshed, and the
# minimum lease a caller may use to reclaim jobs by that column. A single
# provider call can be silent for up to providers.TIMEOUT=600s, so the lease
# must comfortably outlast several missed beats before a live job looks dead.
_HEARTBEAT_INTERVAL = 30.0
_MIN_LEASE_SECONDS = 4 * _HEARTBEAT_INTERVAL

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


def reconcile_jobs(lease_seconds: float | None = None) -> int:
    """Fail-close jobs orphaned by a restart.

    lease_seconds is None (default): blanket mode. A job marked 'running' in
    the DB has no live task after the process that owned it exits (in-process
    queue, not persisted), so at a single-node boot every DB 'running' job
    must be a zombie — mark all of them 'failed' with a terminal event, and
    fail every 'building' artifact too. This is the SQLite single-node path.

    lease_seconds is set: lease mode, for a shared multi-node DB where
    'running' alone can't distinguish a crashed node's zombie from a sibling
    node's live job. Only jobs whose heartbeat is older than
    `now() - lease_seconds` are reclaimed; an artifact stays 'building' as
    long as some job still within its lease backs it.

    Returns how many jobs were reconciled. Idempotent.
    """
    if lease_seconds is None:
        rows = query_all("SELECT id FROM jobs WHERE status = 'running'")
    else:
        if lease_seconds < _MIN_LEASE_SECONDS:
            raise ValueError(
                f"lease_seconds={lease_seconds} is below the minimum "
                f"{_MIN_LEASE_SECONDS} (4x the {_HEARTBEAT_INTERVAL}s heartbeat "
                "interval) and would reap jobs that are alive but merely "
                "between beats")
        cutoff = now() - lease_seconds
        rows = query_all(
            "SELECT id FROM jobs WHERE status = 'running' AND heartbeat < ?", (cutoff,))
    for row in rows:
        nxt = query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM job_events "
                        "WHERE job_id = ?", (row["id"],))["m"] + 1
        try:
            _append_event(row["id"], nxt, "job", "failed",
                          "interrupted by server restart", None)
        except _INTEGRITY_ERRORS:
            pass  # a concurrent reaper already appended this job's terminal event
        execute("UPDATE jobs SET status = 'failed', updated = ? WHERE id = ?",
                (now(), row["id"]))
    if lease_seconds is None:
        # Any artifact still 'building' at startup is orphaned (its job cannot
        # be live), so fail it too. Otherwise it spins forever in the UI and,
        # being non-terminal, can be neither opened nor deleted.
        execute("UPDATE artifacts SET status = 'failed' WHERE status = 'building'")
    else:
        execute(
            "UPDATE artifacts SET status = 'failed' WHERE status = 'building' AND "
            "id NOT IN (SELECT artifact_id FROM jobs WHERE status = 'running' "
            "AND heartbeat >= ? AND artifact_id IS NOT NULL)", (cutoff,))
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
        try:
            _append_event(self._job.id, self._job.seq, stage, status, detail, data)
        except _INTEGRITY_ERRORS:
            pass  # a reaper already appended a terminal event at this seq;
                  # the in-memory event above still reaches live SSE readers
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


async def _beat(job_id: str) -> None:
    """Refresh a running job's heartbeat column periodically so a lease-mode
    reconcile on another node can tell it's alive. Runs for the job's entire
    lifetime -- including time spent queued behind _MAX_CONCURRENT, since the
    row is already status='running' in the DB the moment it's inserted, well
    before the semaphore lets its body actually execute. The status='running'
    guard in the UPDATE keeps a beat that loses a cancellation race from
    reviving a job the runner's finally has already marked terminal.

    This is a best-effort background nicety and must never affect job
    outcome: a transient DB error (locked SQLite file, closed connection at
    shutdown, ...) on one beat is logged and swallowed so the loop keeps
    beating on the next tick, rather than dying and handing an exception to
    whoever awaits this task."""
    while True:
        try:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await asyncio.to_thread(
                execute,
                "UPDATE jobs SET heartbeat = ? WHERE id = ? AND status = 'running'",
                (now(), job_id),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            log.warning("job %s heartbeat update failed; will retry next tick",
                        job_id, exc_info=True)


def _fail_artifact(artifact_id: str) -> None:
    """Best-effort: mark the artifact a job was building as 'failed' so a
    broken stub doesn't sit forever in the artifacts list as 'building'.
    Only downgrades an artifact still 'building': one that already reached
    'ready' (a podcast whose transcript saved before its optional TTS was
    cancelled or died) keeps its good state. Never raises -- this runs from
    inside the job runner's own error handling and must not shadow the
    original failure."""
    try:
        execute("UPDATE artifacts SET status = 'failed' "
                "WHERE id = ? AND status = 'building'", (artifact_id,))
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
        "events_json, created, updated, heartbeat) "
        "VALUES (?, ?, 'running', ?, ?, '[]', ?, ?, ?)",
        (job.id, kind, notebook_id, artifact_id, now(), now(), now()),
    )
    ctx = JobCtx(job)
    log.info("job %s start kind=%s notebook=%s", job.id, kind, notebook_id)

    async def runner() -> None:
        # Started before the semaphore: a job queued behind _MAX_CONCURRENT is
        # already status='running' in the DB (the INSERT above) and must keep
        # beating while it waits, or a lease-mode reconcile on another node
        # could reap it before its body ever runs.
        beat_task = asyncio.get_event_loop().create_task(_beat(job.id))
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
            beat_task.cancel()
            try:
                await beat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The heartbeat is a best-effort nicety (and _beat already
                # swallows non-cancellation errors internally) -- but even a
                # bug there must never be allowed to skip the terminal UPDATE
                # or the sentinel push below, which is what unblocks the SSE
                # generator and lets a completed job's status be observed.
                log.warning("job %s heartbeat task raised on shutdown",
                            job.id, exc_info=True)
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
                event = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"  # SSE comment: keeps the connection alive,
                continue                # ignored by EventSource, no event fires
            if event is _SENTINEL:
                return
            yield frame(event)
    finally:
        if queue in job.queues:
            job.queues.remove(queue)
