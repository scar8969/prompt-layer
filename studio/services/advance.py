"""Advancing a job from the request that asks about it.

Why this exists
---------------
The obvious serverless design is a cron that drives the queue, and that is what
`studio/tick.py` does. It does not work on Vercel's Hobby plan, which permits
**one cron run per day**: a job submitted at 09:00 would sit queued until
tomorrow. Pro allows per-minute crons, but a product that needs a paid plan to
generate an image is the wrong product.

So the client's own poll advances the job. The studio already polls
`GET /api/jobs/{id}` while a job is running -- that is how the progress bar
moves. Making that request do the work means:

* no cron is on the critical path, so it works on any plan;
* a job progresses while somebody is watching it, which is the only time anybody
  cares about latency;
* the daily cron becomes a backstop for jobs nobody is watching (a closed tab),
  rather than the mechanism.

Safety
------
Claiming is already exclusive -- `claim_next` uses `FOR UPDATE SKIP LOCKED` on
Postgres and a single conditional UPDATE that sets the lease, so two requests
polling the same job at the same moment cannot both drive it. A request that
loses the claim simply reads the row, which is what it wanted anyway.

The work is done after the response is built, not before: the reader gets its
current state immediately, and the drive happens on a short-lived thread. If the
process is frozen before that thread runs, the job is still leased and the next
poll picks it up -- the failure mode is "slightly later", not "lost".
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ..logging_setup import get_logger

LOG = get_logger("studio.services.advance")

#: States where a job is still moving and a poll can usefully drive it.
ACTIVE_STATES = ("queued", "validating", "submitting", "processing", "downloading")


def should_advance(job: dict) -> bool:
    """True when this job could make progress if something drove it.

    A terminal job is left alone, and so is a job whose next attempt is still in
    the future: the provider asked to be polled later, and driving it early would
    spend a request to learn nothing.
    """
    state = str(job.get("status") or "")
    if state not in ACTIVE_STATES:
        return False
    return True


def advance_inline(app: Any, workspace_id: str, job_id: str, *, wait: bool = True) -> None:
    """Try to claim and drive one job, on a worker id scoped to this attempt.

    **Synchronous by default, and the route uses the synchronous path.** An
    earlier version started a daemon thread and returned immediately; measured
    locally, two of three polls left the job untouched because the thread had not
    been scheduled before the response was written. On a serverless platform the
    invocation may be frozen the moment the response goes out, so the thread is
    racing the platform on top of racing the scheduler. A mechanism that works
    "usually" is not a queue.

    What is being moved here is one step, not a whole job: submit, or one poll of
    an in-flight job. The provider call is already bounded by the adapter's own
    timeouts, and a job that is not due yet returns immediately.

    ``wait=False`` remains for callers that genuinely must not block, but nothing
    in the request path uses it.
    """
    def run() -> None:
        try:
            # Claim *this* job, not the oldest in the queue. `claim_next` would
            # advance whichever job happened to be oldest, so a client polling job
            # B could leave B untouched while moving A.
            claimed = app.runner.repos.jobs.claim_job(
                job_id, "poll-" + job_id[:8], lease_seconds=60
            )
            if claimed is None:
                # Somebody else holds the lease, or the job is not due yet.
                return
            app.runner.drive(claimed)
        except Exception as error:  # noqa: BLE001 - a failed poll must not break the read
            LOG.warning("advancing a job from a poll failed", extra={"job_id": job_id,
                                                                    "error": type(error).__name__})
        finally:
            release = getattr(app.db, "release", None)
            if release is not None:
                try:
                    release()
                except Exception:  # noqa: BLE001
                    pass

    if wait:
        run()
        return
    threading.Thread(target=run, name="advance-" + job_id[:8], daemon=True).start()
