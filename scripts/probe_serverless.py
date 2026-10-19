"""Probe: does the serverless bridge answer without a socket?

Phase 2 turns the long-lived Studio process into a request-scoped function. The
bridge is only worth anything if the *same* application answers a synchronously
constructed request, so this drives `studio.serverless.handle` directly against a
real Postgres database -- no socket, no server, no threads.

If this works, `api/studio.py` is a thin translation layer and the risk left is
platform wiring, not behaviour.
"""

import sys
import tempfile

sys.path.insert(0, ".")

from studio.config import load_config
from studio.serverless import handle

DATA = tempfile.mkdtemp(prefix="serverless-")
config = load_config(
    {
        "STUDIO_DATA_DIR": DATA,
        "STUDIO_ENV": "development",
        "STUDIO_ENCRYPTION_KEY": "a" * 64,
        "STUDIO_AUTH_SECRET": "b" * 40,
        "STUDIO_ENABLE_MOCK_PROVIDER": "1",
        "STUDIO_PUBLIC_APP_URL": "http://localhost",
        "STUDIO_API_BASE_URL": "http://localhost",
        "STUDIO_DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/studio_serverless",
        "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
        "STUDIO_JOB_WORKERS": "0",
    }
)

from studio.app import build_app

app = build_app(config, start_worker=False)

HEADERS = {"host": "localhost", "accept": "application/json"}


def call(method, path, body=b"", headers=None):
    merged = dict(HEADERS)
    merged.update(headers or {})
    response = handle(method=method, path=path, headers=merged, body=body, app=app)
    return response


print("=== health ===")
r = call("GET", "/api/health")
print("status:", r.status, "| body:", r.body[:120])

print("=== unknown route is a JSON 404, not a crash ===")
r = call("GET", "/api/not-a-route")
print("status:", r.status, "| body:", r.body[:160])

print("=== a malformed body is a 400 ===")
r = call("POST", "/api/auth/register", body=b"{not json", headers={"content-type": "application/json"})
print("status:", r.status, "| body:", r.body[:200])

print("=== response headers survive ===")
r = call("GET", "/api/health")
print("header names:", sorted(r.headers.keys())[:8])

app.shutdown()
print("\nno socket was opened; the same application answered")
