#!/usr/bin/env python3
"""Repro for the assets.list unpacking bug, with the fix.

Creates two assets in one workspace, then lists them. Pre-fix this returned
HTTP 500. Post-fix this returns 200 with both assets.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8091"
PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)

# 1x1 valid PNG built with zlib + crc32
def tiny_png() -> bytes:
    import struct
    import zlib
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )

PNG = tiny_png()


def request(opener, method, path, body=None, csrf=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": BASE,
            "X-CSRF-Token": csrf,
        },
    )
    try:
        with opener.open(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main() -> int:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Register (or login if already exists)
    creds = {"email": "list-repro@example.com", "password": "correct-horse-battery-staple",
             "display_name": "List Repro"}
    status, body = request(opener, "POST", "/api/auth/login", {"email": creds["email"], "password": creds["password"]})
    if status != 200:
        status, body = request(opener, "POST", "/api/auth/register", creds)
        if status != 201:
            print(f"register failed: {status} {body}")
            return 1
    csrf = body["session"]["csrf_token"]
    print(f"logged in: status={status}")

    # Upload two assets
    ids = []
    for name in ("one.png", "two.png"):
        status, body = request(opener, "POST", "/api/assets", {
            "data_base64": base64.b64encode(PNG).decode(),
            "file_name": name,
            "content_type": "image/png",
        }, csrf=csrf)
        if status != 201:
            print(f"upload {name} failed: {status} {body}")
            return 1
        ids.append(body["asset"]["id"])
    print(f"uploaded: {ids}")

    # THE BUG REPRO: list assets. Pre-fix this was 500.
    status, body = request(opener, "GET", "/api/assets")
    print(f"GET /api/assets  status={status}")
    print(f"  next_cursor={body.get('next_cursor')!r}")
    print(f"  asset count={len(body.get('assets') or [])}")
    listed = {a['id'] for a in (body.get('assets') or [])}
    print(f"  uploaded ids present: {set(ids) <= listed}")

    # With a tight limit, get a real cursor
    status, body = request(opener, "GET", "/api/assets?limit=1")
    print(f"GET /api/assets?limit=1  status={status}")
    print(f"  next_cursor={(body.get('next_cursor') or '')[:24]}...")
    if body.get("next_cursor"):
        status, body = request(opener, "GET", f"/api/assets?limit=1&cursor={body['next_cursor']}")
        print(f"  page 2 status={status} count={len(body.get('assets') or [])}")

    if status < 400 and set(ids) <= listed:
        print("PASS: assets.list returns rows and a cursor.")
        return 0
    print("FAIL")
    return 2


if __name__ == "__main__":
    sys.exit(main())
