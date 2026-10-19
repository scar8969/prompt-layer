"""Vercel cron endpoint: one pass of generation work per invocation.

Registered in `vercel.json` as `/api/studio-cron` on a schedule. Kept out of the
`/api/studio/*` catch-all because a cron entry needs a stable path the platform
can call on a timer, and because it should be callable independently for a manual
drain.

Auth
----
`CRON_SECRET` guards it. Vercel sends `Authorization: Bearer $CRON_SECRET` on a
scheduled invocation; without the secret configured, the endpoint refuses
everything rather than running unbilled work for whoever finds the URL. A
deployment that forgets the variable gets a 503 naming it, not an open queue.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


from studio.serverless import authorised as _authorised


class handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _run(self, method: str) -> None:
        if method not in ("GET", "POST"):
            self._respond(405, {"error": {"code": "method_not_allowed",
                                          "message": "This endpoint answers GET and POST."}})
            return

        headers = {str(k).lower(): str(v) for k, v in self.headers.items()}
        allowed, reason = _authorised(headers)
        if not allowed:
            status = 503 if "not set" in reason else 403
            self._respond(status, {"error": {"code": "forbidden" if status == 403 else "unavailable",
                                             "message": reason,
                                             "retryable": False}})
            return

        try:
            from studio.tick import run_tick

            # A tick is capped well under the function's maxDuration so the
            # invocation returns a real summary instead of being killed mid-pass
            # with a job holding a lease.
            summary = run_tick(limit=int(os.environ.get("STUDIO_TICK_LIMIT", "3") or 3),
                               budget_seconds=float(os.environ.get("STUDIO_TICK_BUDGET", "45") or 45))
        except Exception as error:  # noqa: BLE001 - a failed tick must report, not crash
            self._respond(500, {"error": {"code": "internal_error",
                                          "message": "The tick could not run.",
                                          "provider_message": type(error).__name__,
                                          "retryable": True}})
            return

        self._respond(200, summary)

    def do_GET(self) -> None:  # noqa: N802
        self._run("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._run("POST")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return
