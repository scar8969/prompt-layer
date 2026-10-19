import sys, uuid, traceback
sys.path.insert(0, ".")
from studio.db import Database
from studio.repository import repositories

db = Database(url="postgresql://postgres@127.0.0.1:5433/claim_test")
db.migrate()
repos = repositories(db)

STAMP = "2026-01-01T00:00:00.000000Z"
tag = uuid.uuid4().hex[:8]
user = str(uuid.uuid4())
db.insert("users", {"id": user, "email": "s-%s@x.t" % tag,
                    "email_normalized": "s-%s@x.t" % tag,
                    "password_hash": "x", "created_at": STAMP, "updated_at": STAMP})
ws = str(uuid.uuid4())
db.insert("workspaces", {"id": ws, "name": "W", "slug": "s" + tag,
                         "owner_user_id": user, "created_at": STAMP, "updated_at": STAMP})

job = repos.jobs.create(ws, user, {"modality": "image", "mode": "text_to_image", "prompt": "one"})
print("created job:", job["id"], job["status"])

try:
    claimed = repos.jobs.claim_next("w0", lease_seconds=60)
    print("claim_next ->", claimed["id"] if claimed else None,
          "| status:", claimed.get("status") if claimed else None)
    print("second claim (should be None or a different job):",
          (lambda c: c["id"] if c else None)(repos.jobs.claim_next("w1", lease_seconds=60)))
except Exception:
    traceback.print_exc()

db.close_all()
