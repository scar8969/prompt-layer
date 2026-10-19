"""Probe: can two Postgres workers claim the same job?

The concern: `JobRepository.claim_next` documents its safety in terms of
``BEGIN IMMEDIATE`` acquiring SQLite's write lock. On Postgres the transaction
opens with plain ``BEGIN``, so the mechanism the docstring describes does not
exist. Whether that is a real bug depends on how Postgres re-evaluates a locking
statement, and the only way to know is to run it.

This script inserts N queued jobs, then has W workers hammer claim_next
concurrently, and reports how many distinct job ids were handed out versus how
many claims were made. A job claimed twice is a job that runs twice.
"""

import sys
import threading
import uuid

sys.path.insert(0, ".")
from studio.db import Database
from studio.repository import repositories

URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql://postgres@127.0.0.1:5433/claim_test"
JOBS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 6
TRIALS = int(sys.argv[4]) if len(sys.argv) > 4 else 3

db = Database(url=URL)
db.migrate()
repos = repositories(db)

STAMP = "2026-01-01T00:00:00.000000Z"
user = str(uuid.uuid4())
tag = uuid.uuid4().hex[:8]
db.insert("users", {"id": user, "email": "claim-%s@example.test" % tag,
                    "email_normalized": "claim-%s@example.test" % tag,
                    "password_hash": "x", "created_at": STAMP, "updated_at": STAMP})
workspace = str(uuid.uuid4())
db.insert("workspaces", {"id": workspace, "name": "W", "slug": "cl" + workspace[:8],
                         "owner_user_id": user, "created_at": STAMP, "updated_at": STAMP})

print("generation_jobs columns available:", len(db.table_columns("generation_jobs")))

worst = 0
for trial in range(TRIALS):
    job_ids = []
    for index in range(JOBS):
        job_id = str(uuid.uuid4())
        job_ids.append(job_id)
        repos.jobs.create(
            workspace,
            user,
            {
                "modality": "image",
                "mode": "text_to_image",
                "prompt": "probe %d" % index,
            },
        )

    claimed = []
    lock = threading.Lock()

    def worker(name):
        try:
            for _ in range(JOBS):
                job = repos.jobs.claim_next(name, lease_seconds=60)
                if job:
                    with lock:
                        claimed.append(job["id"])
                else:
                    break
        finally:
            # Mirror what GenerationRunner now does each pass: a worker thread
            # that keeps its connection checked out holds the pool forever.
            db.release()

    threads = [threading.Thread(target=worker, args=("w%d" % n,)) for n in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)

    duplicates = len(claimed) - len(set(claimed))
    distinct = len(set(claimed))
    worst = max(worst, duplicates)
    print("trial %d: jobs=%d claims=%d distinct=%d DUPLICATES=%d"
          % (trial + 1, JOBS, len(claimed), distinct, duplicates))

    # reset for the next trial
    db.execute("DELETE FROM generation_jobs")

db.close_all()
print()
print("worst duplicate count across trials:", worst)
sys.exit(1 if worst else 0)
