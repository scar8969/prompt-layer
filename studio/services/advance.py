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

    Synchronous when *wait* is true, which is what the synchronous callers (the
    probe scripts, and a deployment with no threads to spare) want. The route
    passes ``wait=False`` so the response is not held up.
    """
    def run() -> None:
        try:
            claimed = app.runner.repos.jobs.claim_next(
                "poll-" + job_id[:8], lease_seconds=60
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
