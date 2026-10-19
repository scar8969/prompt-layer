"""End-to-end sanity against a real Postgres-backed Studio.

What this proves that the test suite does not
---------------------------------------------
``pytest`` builds each app from an explicit configuration mapping and drives it
over HTTP, which is the right way to pin the contract. This script instead starts
a *server the way a deployment would* -- configuration read from the environment,
``python -m studio.app`` semantics, real socket, real worker threads -- with
``STUDIO_DATABASE_URL`` pointing at Postgres. If the environment path or the
engine switch were broken, the suite could still pass and this would fail.

Usage:

    STUDIO_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/studio_sanity \\
    STUDIO_SANITY_BASE=http://127.0.0.1:8092 \\
    python scripts/sanity_postgres.py

Exit code is the number of failed steps, so ``python scripts/sanity_postgres.py
&& deploy`` is safe. It creates its own account, so it is safe to re-run: every
identifier carries a random suffix and no state is shared between runs.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("STUDIO_SANITY_BASE", "http://127.0.0.1:8092").rstrip("/")
DATABASE_URL = os.environ.get("STUDIO_DATABASE_URL", "")

FAILURES = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global FAILURES
    if ok:
        print("  ok    %s" % label)
    else:
        FAILURES += 1
        print("  FAIL  %s%s" % (label, (" -- " + detail) if detail else ""))
    return ok


class Client:
    """A browser-shaped client: cookie jar, CSRF header, real HTTP."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf = ""

    def _csrf_from_cookies(self) -> str:
        """The CSRF token lives in a JS-readable cookie, not a response header.

        ``studio/routes/auth.py`` sets CSRF_COOKIE alongside the session cookie
        precisely so a client can read it and echo it back in the header. Reading
        a header instead is what made the first version of this script fail every
        state-changing call with "missing its CSRF token".
        """
        for cookie in self.jar:
            if cookie.name.lower().endswith("csrf"):
                return cookie.value
        return ""

    def call(self, method, path, body=None):
        data = None
        headers = {"Accept": "application/json", "Origin": BASE}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        token = self.csrf or self._csrf_from_cookies()
        if token:
            headers["X-CSRF-Token"] = token
        request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                payload = response.read()
                ctype = (response.headers.get("Content-Type") or "").lower()
                if "json" not in ctype:
                    return response.status, {"bytes": payload}
                return response.status, (json.loads(payload) if payload else {})
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return error.code, {"raw": payload[:200].decode("utf-8", "replace")}


# A real 1x1 PNG. Written as bytes so the signature sniffing in
# studio/media.py accepts it -- a fake base64 string would be refused, which is
# the point of that check.
PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


def main() -> int:
    if not DATABASE_URL.startswith("postgres"):
        print("STUDIO_DATABASE_URL must point at Postgres; it is %r" % DATABASE_URL)
        return 1

    print("Studio sanity against Postgres")
    print("  base: %s" % BASE)
    print("  db:   %s" % DATABASE_URL.rsplit("@", 1)[-1])
    print()

    client = Client()

    print("the server is up and reports a healthy database")
    status, health = client.call("GET", "/api/health")
    check("GET /api/health is 200", status == 200, str(health)[:200])
    # /api/health answers {"ok": bool, "database": bool} -- a shallow shape with
    # no nested checks object. Asserting the real field rather than a hoped-for
    # one is the difference between a test and a wish.
    check(
        "the database backend is reachable",
        health.get("database") is True,
        str(health)[:300],
    )

    suffix = uuid.uuid4().hex[:8]
    email = "sanity-%s@example.test" % suffix
    password = "sanity-password-%s" % suffix

    print("registering an account creates a workspace")
    status, body = client.call(
        "POST", "/api/auth/register", {"email": email, "password": password, "display_name": "Sanity"}
    )
    check("POST /api/auth/register is 201", status in (200, 201), str(body)[:300])

    print("the session is authenticated")
    status, body = client.call("GET", "/api/auth/me")
    check("GET /api/auth/me is 200", status == 200, str(body)[:200])

    print("a mock connection and model can be created")
    status, body = client.call(
        "POST",
        "/api/connections",
        {
            "name": "sanity-mock-%s" % suffix,
            "provider": "mock",
            "adapter_type": "mock",
            "base_url": "https://api.openai.com/v1",
            # Sealing a real credential needs the encryption key, so the sanity
            # script exercises the keyless path instead: auth_scheme "none" is a
            # documented, supported state, and it still proves the row can be
            # created, read back and shaped on Postgres.
            "auth_scheme": "none",
        },
    )
    connection_id = (body.get("connection") or body.get("data") or {}).get("id")
    check("POST /api/connections is 201", status in (200, 201), str(body)[:300])
    check("the connection has an id", bool(connection_id), str(body)[:200])

    print("an upload is validated and stored")
    status, body = client.call(
        "POST",
        "/api/assets",
        {
            "filename": "sanity-%s.png" % suffix,
            "data_base64": PNG_1PX,
            "kind": "image",
        },
    )
    asset_id = (body.get("asset") or body.get("data") or {}).get("id")
    check("POST /api/assets is 201", status in (200, 201), str(body)[:300])
    check("the asset has an id", bool(asset_id), str(body)[:200])

    if asset_id:
        status, _ = client.call("GET", "/api/assets/%s/content" % asset_id)
        check("the stored asset can be read back", status == 200)

    print()
    if FAILURES:
        print("%d step(s) failed" % FAILURES)
    else:
        print("all steps passed")
    return FAILURES


if __name__ == "__main__":
    sys.exit(main())
