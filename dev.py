#!/usr/bin/env python3
"""Local emulation of this project on Vercel.

Vercel serves the repo root as static files and mounts each file in /api as its
own function exporting `handler`. This does the same on one port:

  * a static server on PORT (default 8090) serving the built client in web/dist
  * one HTTP server per file in /api, running that file's real `handler` class
  * /api/<name> is proxied to the matching function server

One server per function is not a workaround: it is what Vercel does, and it means
what you exercise locally is the deployed entry point rather than a
re-implementation. `python -m pytest -q` asserts the same handlers directly.

    python dev.py            # http://127.0.0.1:8090

Not part of the deployment — Vercel never runs this file.
"""

from __future__ import annotations

import importlib.util
import mimetypes
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.join(ROOT, "api")
PORT = int(os.environ.get("PORT", "8090"))
UPSTREAM: dict = {}


def start_function_servers() -> dict:
    """Mount every /api file on its own server, as the platform effectively does."""
    ports = {}
    for filename in sorted(os.listdir(API_DIR)):
        if not filename.endswith(".py"):
            continue
        name = filename[:-3]
        spec = importlib.util.spec_from_file_location(f"vf_{name}", os.path.join(API_DIR, filename))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), module.handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ports[name] = srv.server_address[1]
    return ports


class Front(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        name = parsed.path[len("/api/"):].strip("/").split("/")[0]
        port = UPSTREAM.get(name)
        if port is None:
            return self._reply(404, b'{"success":false,"message":"no such function"}',
                               "application/json")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        target = f"http://127.0.0.1:{port}{parsed.path}"
        if parsed.query:
            target += "?" + parsed.query
        req = urllib.request.Request(
            target,
            data=body,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload, code = resp.read(), resp.status
                ctype = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            payload, code, ctype = exc.read(), exc.code, "application/json"
        except Exception as exc:
            payload = ('{"success":false,"message":"function unreachable: %s"}' % exc).encode()
            code, ctype = 502, "application/json"
        self._reply(code, payload, ctype)

    def _static(self) -> None:
        """The built client, exactly as the deployed function serves it.

        `web/dist` is the product's only front door: there is no second page in
        the repository root any more. A path with no extension that is not a
        file falls back to index.html, because the client routes in the browser.
        """
        path = urllib.parse.urlparse(self.path).path
        root = os.path.join(ROOT, "web", "dist")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        candidate = os.path.abspath(os.path.join(root, rel))
        if candidate != os.path.abspath(root) and not candidate.startswith(os.path.abspath(root) + os.sep):
            return self._reply(403, b"forbidden", "text/plain")
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "index.html")
        if not os.path.isfile(candidate):
            if "." in os.path.basename(rel):
                return self._reply(404, f"not found: {rel}".encode(), "text/plain")
            candidate = os.path.join(root, "index.html")
        if not os.path.isfile(candidate):
            return self._reply(
                404,
                b"web/dist is missing. Run `npm run build` in web/ first.",
                "text/plain",
            )
        ctype = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(candidate, "rb") as fh:
            self._reply(200, fh.read(), ctype)

    def do_GET(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            return self._proxy("GET")
        return self._static()

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            return self._proxy("POST")
        return self._static()

    def do_OPTIONS(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            return self._proxy("OPTIONS")
        return self._reply(204, b"", "text/plain")

    def log_message(self, fmt, *args):
        print("dev:", fmt % args)


if __name__ == "__main__":
    UPSTREAM.update(start_function_servers())
    print(f"http://127.0.0.1:{PORT}")
    print("  functions: " + ", ".join(f"/api/{n} -> :{p}" for n, p in sorted(UPSTREAM.items())))
    ThreadingHTTPServer(("127.0.0.1", PORT), Front).serve_forever()
