"""End-to-end: submit over HTTP, then let one cron tick finish the job.

This is the deployment's actual job lifecycle:

    POST /api/studio/api/generations/images   (the function)
    -> row queued
    cron tick                                  (the scheduled function)
    -> row submitted / completed with an asset

No worker thread is involved, which is the point: on Vercel there is nowhere for
one to live. Both halves go through the same code the deployment runs.
"""

import json
import os
import sys
import uuid

sys.path.insert(0, ".")

from studio.app import build_app
from studio.config import load_config
from studio.serverless import handle
from studio.tick import run_tick

VALUES = {
    "STUDIO_DATA_DIR": r"C:\Users\priya\AppData\Local\Temp\tickE2E",
    "STUDIO_ENV": "development",
    "STUDIO_ENCRYPTION_KEY": "a" * 64,
    "STUDIO_AUTH_SECRET": "b" * 40,
    "STUDIO_ENABLE_MOCK_PROVIDER": "1",
    "STUDIO_PUBLIC_APP_URL": "http://localhost",
    "STUDIO_API_BASE_URL": "http://localhost",
    "STUDIO_DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/studio_ticke2e",
    "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
    "STUDIO_CREDENTIAL_RATE_LIMIT_PER_MINUTE": "100000",
    "STUDIO_JOB_WORKERS": "0",
}
os.environ.update(VALUES)

app = build_app(load_config(VALUES), start_worker=False)

csrf = {"token": ""}
cookies = {}


def call(method, path, body=None):
    headers = {"host": "localhost", "accept": "application/json", "origin": "http://localhost"}
    if body is not None:
        headers["content-type"] = "application/json"
    if cookies:
        headers["cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())
    if csrf["token"] and method != "GET":
        headers["x-csrf-token"] = csrf["token"]
    payload = b"" if body is None else json.dumps(body).encode()
    response = handle(method=method, path=path, headers=headers, body=payload, app=app)
    for name, value in (response.headers or {}).items():
        if name.lower() == "set-cookie":
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                pair = str(item).split(";", 1)[0]
                if "=" in pair:
                    key, _, val = pair.partition("=")
                    cookies[key.strip()] = val.strip()
                    if "csrf" in key.lower():
                        csrf["token"] = val.strip()
    try:
        return response.status, json.loads(response.body or b"{}")
    except Exception:
        return response.status, {"raw": response.body[:200]}


tag = uuid.uuid4().hex[:8]
print("register")
status, body = call("POST", "/api/auth/register",
                    {"email": "e2e-%s@example.test" % tag,
                     "password": "e2e-password-%s" % tag,
                     "display_name": "E2E"})
print("  ", status, str(body)[:120])
csrf["token"] = csrf["token"] or cookies.get("studio_csrf", "")
print("  csrf seen:", bool(csrf["token"]), "| cookies:", list(cookies))

print("connection (keyless mock)")
status, body = call("POST", "/api/connections", {
    "name": "mock-" + tag, "provider_slug": "mock", "adapter_type": "mock",
    "base_url": "https://api.openai.com/v1", "auth_scheme": "none"})
conn = (body.get("connection") or body.get("data") or {})
print("  ", status, conn.get("id") or json.dumps(body)[:300])

print("model (registered on the connection, not at /api/models)")
status, body = call("POST", "/api/connections/%s/models" % conn.get("id"), {
    "model_key": "mock/placeholder-image",
    "display_name": "Mock image", "modality": "image",
    "modes": ["text_to_image"], "source": "manual"})
model = (body.get("model") or body.get("data") or {})
print("  ", status, model.get("id") or json.dumps(body)[:300])

print("submit a generation")
status, body = call("POST", "/api/generations/images", {
    "model_key": "mock/placeholder-image", "connection_id": conn.get("id"),
    "prompt": "a tick end to end", "mode": "text_to_image"})
job = (body.get("job") or body.get("data") or {})
print("  ", status, job.get("id"), job.get("status"))

if not job.get("id"):
    print("\nsubmit failed:", json.dumps(body)[:400])
    app.shutdown()
    raise SystemExit(1)

print("draining with ticks")
for n in range(6):
    summary = run_tick(limit=3, budget_seconds=30)
    row = app.db.query_dict(
        "SELECT status, error_code, error_message, provider_job_ref FROM generation_jobs WHERE id = ?",
        (job["id"],))
    print("  tick %d: %s | status=%s%s" % (
        n + 1, summary, row.get("status"),
        "" if not row.get("error_code") else " err=%s: %s" % (row["error_code"], row["error_message"])))
    if row.get("status") in ("completed", "failed", "cancelled"):
        break

outputs = app.db.query("SELECT id, storage_key FROM generation_outputs WHERE job_id = ?", (job["id"],))
print("\noutputs:", len(outputs), [dict(o) for o in outputs][:2])
app.shutdown()
print("\nfinal:", row.get("status"))
