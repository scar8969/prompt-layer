#!/usr/bin/env python3
"""End-to-end smoke test for Studio using only stdlib.

Runs against the live server on http://127.0.0.1:8091. Uses the mock adapter so
no real provider is touched. Prints one line per stage with the result.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Dict, Tuple

BASE = "http://127.0.0.1:8091"
COOKIES = CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIES))
CSRF = ""


def request(method: str, path: str, body: Dict[str, Any] | None = None, *, expect=(200,)) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": BASE,
            "X-CSRF-Token": CSRF,
        },
    )
    if isinstance(expect, int):
        expect_set = {expect}
    else:
        expect_set = set(expect)
    try:
        with OPENER.open(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
            try:
                parsed = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                parsed = payload
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = payload
        if exc.code in expect_set:
            return exc.code, parsed
        # On 5xx, surface the body so the failure is debuggable.
        if exc.code >= 500:
            sys.stderr.write(f"!! {method} {path} -> HTTP {exc.code}: {payload[:500]}\n")
        raise


def show(label: str, status: int, body: Any) -> None:
    short = "OK" if status < 400 else f"HTTP {status}"
    if isinstance(body, dict):
        keys = list(body.keys())
        print(f"[{short:>6}] {label:35s} status={status} keys={keys[:6]}")
    else:
        s = (body or "")[:120] if isinstance(body, str) else str(body)[:120]
        print(f"[{short:>6}] {label:35s} status={status} body={s}")


def main() -> int:
    global CSRF

    print("== status ==")
    status, body = request("GET", "/api/system/status")
    show("system/status", status, body)
    assert status == 200

    print("\n== register (idempotent) ==")
    creds = {"email": "smoke@example.com", "password": "correct-horse-battery-staple", "display_name": "Smoke Tester"}
    status, body = request("POST", "/api/auth/login", {"email": creds["email"], "password": creds["password"]})
    if status == 200:
        show("login", status, body)
        CSRF = body["session"]["csrf_token"]
    else:
        status, body = request("POST", "/api/auth/register", creds)
        if status == 201:
            show("register", status, body)
            CSRF = body["session"]["csrf_token"]
        else:
            raise SystemExit(f"register failed: {status} {body}")

    print("\n== me ==")
    status, body = request("GET", "/api/auth/me")
    show("auth/me", status, body)
    assert status == 200
    workspace_id = body["workspace"]["id"]
    print(f"      workspace_id = {workspace_id}")

    print("\n== connection types ==")
    status, body = request("GET", "/api/connections/types")
    show("connections/types", status, body)
    assert status == 200
    print(f"      adapters: {[a['slug'] for a in body['types']]}")

    print("\n== create mock connection ==")
    conn_body = {
        "name": "Local mock",
        "adapter_type": "mock",
        "base_url": "http://localhost:8091/mock",
        "auth_scheme": "none",
        "credential": "",
    }
    status, body = request("POST", "/api/connections", conn_body, expect=409)
    if status == 201:
        show("connections.create", status, body)
        connection = body["connection"]
    elif status == 409:
        print("[409 ] connections.create                    name taken, listing to reuse")
        status, list_body = request("GET", "/api/connections")
        connection = next(a for a in list_body["connections"] if a["name"] == "Local mock")
    else:
        raise SystemExit(f"create failed: {status} {body}")
    connection_id = connection["id"]
    print(f"      connection_id = {connection_id}")
    print(f"      last4={connection['credential'].get('last4')!r}")

    print("\n== test connection ==")
    status, body = request("POST", f"/api/connections/{connection_id}/test")
    show("connections.test", status, body)
    assert status == 200
    # /api/connections/{id}/test returns {"test": {...}} per the contract
    test_payload = body.get("test", body)
    ok = test_payload.get("ok") if isinstance(test_payload, dict) else None
    if ok is False:
        print(f"      test reported not-ok: {test_payload}")
    else:
        print(f"      test.ok = {ok}")

    print("\n== add a mock model ==")
    model_body = {
        "model_key": "mock/placeholder-image",
        "display_name": "Placeholder Image",
        "modality": "image",
        "modes": ["text_to_image"],
        "parameter_schema": {"parameters": []},
        "source": "user",
    }
    status, body = request("POST", f"/api/connections/{connection_id}/models", model_body, expect=(400, 409))
    if status == 201:
        show("models.add", status, body)
        model = body["model"]
    else:
        print(f"[{status}] models.add  body={json.dumps(body)[:300] if body else 'empty'}")
        # Already added in a previous run; list and reuse.
        status, list_body = request("GET", f"/api/connections/{connection_id}/models")
        models_list = list_body.get("models", []) if isinstance(list_body, dict) else []
        if not models_list:
            raise SystemExit(f"no models on connection {connection_id}: {list_body}")
        model = models_list[0]
        show("models.list", status, list_body)
    model_id = model["id"]
    print(f"      model_id = {model_id}")
    print(f"      is_mock  = {model.get('is_mock')}")

    print("\n== generation submit ==")
    gen_body = {
        "model_id": model_id,
        "mode": "text_to_image",
        "prompt": "a single white daisy on a black background, soft light",
        "parameters": {},
    }
    # The route returns 201 on first submit. On rerun with the same idempotency
    # key it can return the existing job — both are success for our purposes.
    status, body = request("POST", "/api/generations/images", gen_body, expect=409)
    show("generations.images", status, body)
    assert status in (200, 201, 202), (status, body)
    job_id = body["job"]["id"]
    print(f"      job_id   = {job_id}")
    print(f"      status   = {body['job']['status']}")
    if body.get("asset"):
        print(f"      asset_id = {body['asset']['id']}  bytes={body['asset'].get('bytes')}")

    print("\n== job history ==")
    status, body = request("GET", f"/api/jobs/{job_id}")
    show("jobs.get", status, body)
    if body.get("events"):
        for ev in body["events"][-5:]:
            print(f"        - {ev.get('state')} at {ev.get('occurred_at')}")

    print("\n== wait for worker to complete the job ==")
    import time
    deadline = time.time() + 30
    job = None
    while time.time() < deadline:
        time.sleep(1)
        status, body = request("GET", f"/api/jobs/{job_id}")
        if status == 200:
            job = body
            state = (job.get("job") or job).get("status") if isinstance(job, dict) else None
            if state in ("completed", "failed", "cancelled"):
                break
    final_state = (job.get("job") if isinstance(job, dict) else job)
    if isinstance(final_state, dict):
        print(f"      final status: {final_state.get('status')}")
        print(f"      final message: {final_state.get('status_message')}")
        print(f"      outputs: {len(final_state.get('outputs') or [])}")
        print(f"      attempts: {final_state.get('attempt')}")
    else:
        print(f"      poll response: {job}")

    print("\n== list assets ==")
    status, body = request("GET", "/api/assets")
    show("assets.list", status, body)
    if body.get("assets"):
        a = body["assets"][0]
        print(f"      asset[0]: id={a['id']} type={a.get('media_type')} mock={a.get('is_mock')}")
        print(f"      asset[0]: width={a.get('width')} height={a.get('height')} bytes={a.get('bytes')}")
        # Try fetching the asset bytes to prove storage works end-to-end
        asset_id = a["id"]
        try:
            req = urllib.request.Request(
                BASE + f"/api/assets/{asset_id}/content",
                headers={"Origin": BASE},
            )
            with OPENER.open(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type")
                content_bytes = resp.read()
                print(f"      asset[0].content: {content_type}, {len(content_bytes)} bytes")
        except urllib.error.HTTPError as exc:
            print(f"      asset[0].content: HTTP {exc.code}")

    print("\n== usage ==")
    status, body = request("GET", "/api/usage")
    show("usage", status, body)

    print("\n== list connections (final) ==")
    status, body = request("GET", "/api/connections")
    show("connections.list", status, body)

    print("\nAll smoke stages completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())