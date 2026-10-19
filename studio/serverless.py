"""The bridge from a Vercel function invocation to the Studio HTTP application.

Why this file exists
--------------------
Studio was built as a long-lived process: `python -m studio.app` opens a socket and
`BaseHTTPRequestHandler` reads requests off it. A Vercel function is the opposite
shape -- one invocation, one request, and the process may be frozen or discarded
afterwards.

The two do share everything that matters. `HttpApp.handle(RequestContext) ->
Response` is the entire application, and `studio.http.make_request` already knows
how to turn raw method/path/headers/body into the `RequestContext` the socket
handler builds. So the bridge is small on purpose: construct the same application
object, build the same context, call the same handle, and translate the
`Response` into whatever the platform wants. Routing, auth, CSRF, rate limiting,
redaction and error envelopes are then *the same code* under test locally and
deployed, not a second implementation that can drift.

What is deliberately not here
-----------------------------
**No worker threads.** `build_app(start_worker=False)` is used, so an invocation
serves HTTP and nothing else. Generation work is driven by the cron tick
(`/api/studio/cron/tick`), because a request-scoped process that started a
polling loop would be killed mid-job and the loop would never resume.

**No state carried between invocations.** A module-level cache would be a lie on
this platform: a warm instance is an implementation detail, and an instance can be
frozen between requests. The one exception is the connection pool, which is
created per instance and is safe to recreate -- see `_APPLICATION`.

Cost of that decision
---------------------
Each cold start re-runs `build_app`, which includes `db.migrate()`. Migrations are
idempotent and recorded in `schema_migrations`, so the second call is one SELECT.
A warm instance reuses the application for the life of the instance.
"""

from __future__ import annotations

import hmac
import os
import sys
import threading
from typing import Any, Optional

# A Vercel function's working directory is the project root, but only when it is
# invoked for a path under api/. Being explicit keeps `from studio...` importable
# regardless of how the platform sets cwd.
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from studio.app import StudioApp, build_app  # noqa: E402
from studio.http import make_request  # noqa: E402

_APPLICATION: Optional[StudioApp] = None
_LOCK = threading.Lock()


def application() -> StudioApp:
    """The application for this instance, built on first use.

    The lock matters: a cold instance can receive several invocations at once, and
    two of them running `build_app` simultaneously would both try to open a pool
    and apply migrations. Building once under a lock turns that into a wait.
    """
    global _APPLICATION
    if _APPLICATION is None:
        with _LOCK:
            if _APPLICATION is None:
                _APPLICATION = build_app(start_worker=False)
    return _APPLICATION


def reset_application() -> None:
    """Drop the cached application. Used by tests to isolate one from another."""
    global _APPLICATION
    with _LOCK:
        if _APPLICATION is not None:
            try:
                _APPLICATION.shutdown()
            except Exception:  # noqa: BLE001 - shutdown must never mask a test failure
                pass
            _APPLICATION = None


def client_ip_from(headers: dict) -> str:
    """The caller's address, as the platform reports it.

    Vercel sets `x-forwarded-for` with the client first and any proxies after it.
    Falling back to a constant rather than to "unknown" keeps the rate limiter's
    key stable: a per-request random value would let one caller past every limit.
    """
    forwarded = (headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = (headers.get("x-real-ip") or "").strip()
    return real or "0.0.0.0"


def application_path(raw_path: str, headers: dict, prefix: str = "/api/studio") -> str:
    """The path Studio should route on, with the mount prefix removed.

    `/api/studio/api/health` -> `/api/health`

    The prefix exists because this repository already serves the Prompt Layer
    functions at `/api/health`, `/api/models` and `/api/presets`; Studio's routes
    would shadow them. Keeping the translation here rather than in `api/studio.py`
    means it is unit-testable without importing a Vercel function module.

    The platform header is preferred when present because it is what the platform
    actually routed -- a rewrite can otherwise hand over a path we did not expect.
    The raw path is the fallback, so a direct invocation works too.
    """
    original = (headers.get("x-vercel-proxy-path") or "").strip()
    if original:
        if original.startswith(prefix):
            remainder = original[len(prefix):]
            return remainder or "/"
        return original
    path = raw_path or "/"
    if path.startswith(prefix):
        remainder = path[len(prefix):]
        return remainder or "/"
    return path


def handle(
    *,
    method: str,
    path: str,
    headers: dict,
    body: bytes = b"",
    app: Optional[StudioApp] = None,
) -> Any:
    """Run one request through the real application and return its Response.

    `path` is the path the *application* should see, which is not necessarily the
    path the platform invoked: the function is mounted at `/api/studio/...` while
    the application routes on `/api/...`. The caller (the function module) strips
    the prefix before calling.
    """
    studio = app or application()
    request = make_request(
        method=method,
        raw_path=path,
        headers=headers,
        body=body,
        client_ip=client_ip_from(headers),
    )
    return studio.http.handle(request)


def authorised(headers) -> tuple:
    """(allowed, reason). Constant-time compare; a missing secret refuses all.

    Lives in the `studio` package rather than in this module so it can be unit
    tested without importing a Vercel function file (the `api/` directory is not a
    package, and a function module imports fine only when the platform loads it).

    An unset CRON_SECRET refuses everything. The URL is public on a deployed
    site, so the alternative -- running the queue open -- would let anyone drain
    it, and every drained job is a provider call that costs money.
    """
    expected = (os.environ.get("CRON_SECRET") or "").strip()
    if not expected:
        return False, "CRON_SECRET is not set on this deployment."
    supplied = ""
    raw = headers.get("authorization") or ""
    if raw.lower().startswith("bearer "):
        supplied = raw[7:].strip()
    if not supplied:
        supplied = (headers.get("x-cron-secret") or "").strip()
    if not supplied:
        return False, "This endpoint needs the cron secret."
    if not hmac.compare_digest(supplied, expected):
        return False, "The cron secret does not match."
    return True, ""
