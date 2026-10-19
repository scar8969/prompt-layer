"""The bridge from a Vercel cron invocation to one pass of generation work.

Why a cron tick rather than worker threads
------------------------------------------
Studio's queue is a table with leases, and `GenerationRunner.drive()` already
moves one job one step and then hands its lease back with `next_attempt_at` set.
That is exactly the shape a scheduled function needs: claim, drive, return.

A long-lived loop would be wrong here for a plain reason -- a serverless process
is frozen or discarded between invocations. A thread started on invocation one
does not survive to invocation two, so jobs would sit `submitting` until their
lease expired and the retry logic picked them up, which is slower and noisier
than just running the pass on a schedule.

What one tick does
------------------
Claim up to `limit` jobs one at a time and drive each one step, stopping at the
first moment there is no claimable work. `limit` exists because a tick has a
wall-clock budget: `maxDuration` in vercel.json. Doing three jobs well beats
claiming ten and being killed at the deadline, because a killed invocation leaves
a job holding a lease it will not release.

Why the runner is built fresh per tick
--------------------------------------
`build_app(start_worker=False)` gives an application whose worker is deliberately
not started; the tick then calls the same `drive()` the loop would. Nothing about
the pass is serverless-specific, so the tested path is the deployed path.
"""

from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def run_tick(*, limit: int = 3, budget_seconds: float = 45.0) -> dict:
    """Drive up to *limit* jobs, stopping early when the budget runs out.

    Returns a summary rather than raising: a cron caller has nowhere useful to
    put an exception, and the response body is the only record of what happened.
    """
    from studio.app import build_app

    started = time.monotonic()
    app = build_app(start_worker=False)
    runner = app.runner
    worker_id = "cron-" + os.urandom(4).hex()

    claimed = 0
    finished = 0
    errors = []
    try:
        for _ in range(max(0, limit)):
            if time.monotonic() - started > budget_seconds:
                break
            job = runner.repos.jobs.claim_next(
                worker_id, lease_seconds=max(60, int(budget_seconds) * 2)
            )
            if job is None:
                break
            claimed += 1
            try:
                runner.drive(job)
                finished += 1
            except Exception as error:  # noqa: BLE001 - one bad job must not stop the tick
                errors.append({"job_id": str(job.get("id") or ""),
                               "error": type(error).__name__})
    finally:
        # The pool belongs to this invocation. Closing it here means a warm
        # instance does not accumulate pools across ticks.
        try:
            app.shutdown()
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": not errors,
        "worker_id": worker_id,
        "claimed": claimed,
        "driven": finished,
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
