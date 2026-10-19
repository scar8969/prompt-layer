"""Does one job get handed to two workers? On Postgres, it did.

The bug this pins
-----------------
``JobRepository.claim_next`` is an ``UPDATE ... WHERE id = (SELECT ... LIMIT 1)``.
The subquery picks a row; the UPDATE then takes that row's lock. On SQLite the
surrounding ``BEGIN IMMEDIATE`` serialises writers, so the window does not exist.
On Postgres it does: the second worker's UPDATE blocks on the row lock, and when
the first commits, Postgres re-evaluates the *outer* WHERE only -- it does not
re-run the subquery. With a bare ``WHERE id = (...)`` there is nothing for the
outer re-check to refuse, so the same row is updated again.

Measured before the fix: **20 jobs, 6 workers, 48 claims, 28 duplicates.** A job
claimed twice is a job submitted to the provider twice, which is billed twice.
That is why this is a correctness test and not a performance one.

What the fix is
---------------
``FOR UPDATE SKIP LOCKED`` inside the subquery (Postgres only -- SQLite rejects
the syntax), plus the status/lease conditions repeated in the outer WHERE. Either
one alone is sufficient; together they are belt and braces, because the failure
mode is money.

These tests need a real Postgres server and skip without one. They are slow by
design: a concurrency bug that does not reproduce under contention is not being
tested.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.db import Database  # noqa: E402
from studio.repository import repositories  # noqa: E402

STAMP = "2026-01-01T00:00:00.000000Z"


@pytest.fixture()
def queue(studio_database):
    """A migrated Postgres database with one workspace, ready to queue jobs."""
    if not studio_database:
        pytest.skip("set STUDIO_TEST_POSTGRES to run the claim tests")
    db = Database(url=studio_database)
    db.migrate()
    repos = repositories(db)

    tag = uuid.uuid4().hex[:8]
    user = str(uuid.uuid4())
    db.insert("users", {"id": user, "email": "claim-%s@example.test" % tag,
                        "email_normalized": "claim-%s@example.test" % tag,
                        "password_hash": "x", "created_at": STAMP,
                        "updated_at": STAMP})
    workspace = str(uuid.uuid4())
    db.insert("workspaces", {"id": workspace, "name": "W", "slug": "cl" + tag,
                             "owner_user_id": user, "created_at": STAMP,
                             "updated_at": STAMP})
    try:
        yield db, repos, workspace, user
    finally:
        db.close_all()


def _enqueue(repos, workspace, user, count):
    for index in range(count):
        repos.jobs.create(
            workspace, user,
            {"modality": "image", "mode": "text_to_image",
             "prompt": "claim test %d" % index},
        )


class TestClaimingIsExclusiveOnPostgres:
    def test_concurrent_workers_never_receive_the_same_job(self, queue):
        """The measured failure, asserted directly.

        Six workers drain a queue of twenty, repeated over several rounds. Every
        claim must be a distinct job: ``claims == distinct``.

        The rounds are not padding. The underlying race needs a worker to block on
        a row lock at exactly the moment another commits, so a single round
        reproduces it perhaps three times in four; the broken code was caught in
        3 of 4 single-round runs. Three rounds makes a miss unlikely enough to
        trust, and the loop exits as soon as it finds a duplicate.
        """
        db, repos, workspace, user = queue
        jobs, workers, rounds = 20, 6, 3

        for round_number in range(rounds):
            _enqueue(repos, workspace, user, jobs)

            claimed = []
            lock = threading.Lock()

            def worker(name):
                try:
                    for _ in range(jobs):
                        job = repos.jobs.claim_next(name, lease_seconds=60)
                        if job is None:
                            break
                        with lock:
                            claimed.append(job["id"])
                finally:
                    # A worker thread that keeps its connection holds the pool.
                    db.release()

            threads = [threading.Thread(target=worker, args=("w%d" % n,))
                       for n in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
                assert not thread.is_alive(), "a worker thread hung"

            assert len(claimed) == jobs, (
                "round %d: expected every job to be claimed exactly once, got %d claims"
                % (round_number + 1, len(claimed))
            )
            assert len(set(claimed)) == len(claimed), (
                "round %d: %d claims for %d distinct jobs -- a job was handed out twice"
                % (round_number + 1, len(claimed), len(set(claimed)))
            )

            # Clear the queue between rounds so each round starts clean.
            db.execute("DELETE FROM generation_jobs")

    def test_the_same_job_is_not_claimable_twice_in_a_row(self, queue):
        """The single-threaded shape of the same bug, as a plain assertion."""
        db, repos, workspace, user = queue
        _enqueue(repos, workspace, user, 1)

        first = repos.jobs.claim_next("worker-a", lease_seconds=60)
        assert first is not None
        second = repos.jobs.claim_next("worker-b", lease_seconds=60)
        assert second is None or second["id"] != first["id"]

    def test_a_claimed_job_records_the_owner_and_a_lease(self, queue):
        db, repos, workspace, user = queue
        _enqueue(repos, workspace, user, 1)
        job = repos.jobs.claim_next("worker-a", lease_seconds=60)
        row = db.query_dict(
            "SELECT lease_owner, lease_expires_at, status, attempt FROM generation_jobs"
            " WHERE id = ?", (job["id"],)
        )
        assert row["lease_owner"] == "worker-a"
        assert row["lease_expires_at"]
        assert row["status"] == "submitting"
        assert int(row["attempt"]) == 1

    def test_an_expired_lease_makes_the_job_claimable_again(self, queue):
        """A worker that dies without releasing must not strand its job."""
        db, repos, workspace, user = queue
        _enqueue(repos, workspace, user, 1)
        first = repos.jobs.claim_next("worker-a", lease_seconds=60)
        assert first is not None

        # Simulate the worker dying: its job would be retried only once the lease
        # lapses. Two statuses can be reclaimed -- 'submitting' (never sent) and
        # 'downloading' (sent, poll it) -- so put it in the poll-able one.
        db.execute(
            "UPDATE generation_jobs SET status = 'processing',"
            " lease_expires_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
            (first["id"],),
        )
        again = repos.jobs.claim_next("worker-b", lease_seconds=60)
        assert again is not None
        assert again["id"] == first["id"]
        assert again["status"] == "processing"
        # Claiming to poll must not consume an attempt: attempt counts sends to
        # the provider, and polling a job already in flight is not another send.
        row = db.query_dict(
            "SELECT attempt FROM generation_jobs WHERE id = ?", (first["id"],)
        )
        assert int(row["attempt"]) == 1, "polling consumed an attempt"
