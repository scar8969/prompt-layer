"""Vercel function serving Studio at /api/studio/*.

Why one function and not eight
------------------------------
Studio routes on `/api/...`, and the Prompt Layer functions already own
`/api/health`, `/api/models` and `/api/presets` in this same repo. Mounting Studio
on its own prefix keeps the two surfaces from shadowing each other, and one
catch-all function keeps studio's internal routing in exactly one place: the
Router in `studio/app.py`. Splitting by route group would mean re-encoding the
same prefixes in `vercel.json`, where a mismatch is invisible until a 404 in
production.

The prefix is stripped here rather than in the application, so `studio/routes/`
keeps saying `/api/health` and the local server and this function run identical
route tables.

Deployment requirement
----------------------
`vercel.json` must route `/api/studio/(.*)` to this file. The first path segment
after the prefix is restored so the application sees the real path; the platform's
own `x-vercel-proxy-path` is used when present, and the raw path is the fallback.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# Imported at module scope, not inside the request handler. The handler needs
# `application_path` before its try block, and an import placed inside a function
# makes that name function-local for the whole function -- so referring to it
# earlier raises UnboundLocalError. That is exactly what shipped once.
from studio.serverless import application_path, handle  # noqa: E402


def _headers(headers) -> dict:
    lowered = {}
    for key, value in headers.items():
        lowered[str(key).lower()] = str(value)
    return lowered


class handler(BaseHTTPRequestHandler):
    """Vercel's Python entrypoint. One invocation, one request, no loop."""

    protocol_version = "HTTP/1.1"

    def _run(self, method: str) -> None:
        try:
            length = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        body = self.rfile.read(length) if length > 0 else b""

        headers = _headers(self.headers)
        app_path = application_path(self.path, headers)
        if headers.get("x-vercel-proxy-path") and app_path == self.path:
            # Platform rewrite: keep the query string, which the proxy path omits.
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            if query and "?" not in app_path:
                app_path = app_path + "?" + query

        try:
            response = handle(
                method=method,
                path=app_path,
                headers=headers,
                body=body,
            )
        except Exception as error:  # noqa: BLE001 - the invocation must still answer
            # A cold start can fail for reasons the application never sees: no
            # database URL, an unreadable schema, a missing driver. Answer with
            # the same envelope shape the application uses so a client's error
            # handling does not need a second path -- and name what is missing,
            # because "OperationalError" tells whoever deployed this nothing.
            from studio.startup import missing_for_startup

            missing = missing_for_startup()
            self._write(
                500,
                json.dumps(
                    {
                        "error": {
                            "code": "configuration_required" if missing else "internal_error",
                            "message": (
                                "The Studio function is missing the settings it needs to start."
                                if missing
                                else "The Studio function could not start."
                            ),
                            "details": {"missing": missing} if missing else None,
                            "provider_message": type(error).__name__,
                            "retryable": False,
                        }
                    }
                ).encode("utf-8"),
                "application/json; charset=utf-8",
                {"X-Request-Id": ""},
            )
            return

        self._write(
            response.status,
            response.body,
            response.content_type,
            response.headers,
            method,
        )

    def _write(self, status, body, content_type, headers, method="GET") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body or b"")))
            for name, value in (headers or {}).items():
                if str(name).lower() in ("content-type", "content-length"):
                    continue
                if isinstance(value, (list, tuple)):
                    # Repeated header. Two Set-Cookie values travel this way.
                    for item in value:
                        self.send_header(name, str(item))
                else:
                    self.send_header(name, str(value))
            self.end_headers()
            if method.upper() != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802
        self._run("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._run("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._run("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._run("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._run("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._run("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._run("OPTIONS")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Silence the default access log; Studio logs its own structured lines."""
        return
