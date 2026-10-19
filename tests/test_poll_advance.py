"""Phase 6: a poll advances a job, so the queue needs no per-minute cron.

Vercel's Hobby plan allows one cron run per day, which rules out the tick as the
mechanism. The mechanism is the client's own poll: `GET /api/jobs/{id}` claims and
drives the job it is asked about.

This suite asserts that end to end over HTTP against a real application, because
the property is "a user watching a job makes it finish" and nothing smaller than a
request proves it.

Needs Postgres (the claim uses SKIP LOCKED) and skips without it.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.serverless import handle  # noqa: E402
from studio.services.advance import ACTIVE_STATES, should_advance  # noqa: E402

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


@pytest.fixture(scope="module")
def host(studio_database, tmp_path_factory):
    """A real application served through the serverless bridge."""
    if not studio_database:
        pytest.skip("set STUDIO_TEST_POSTGRES for the poll-advance tests")
    data_dir = str(tmp_path_factory.mktemp("advance"))
    values = {
        "STUDIO_DATA_DIR": data_dir,
        "STUDIO_ENV": "development",
        "STUDIO_ENCRYPTION_KEY": "a" * 64,
        "STUDIO_AUTH_SECRET": "b" * 40,
        "STUDIO_ENABLE_MOCK_PROVIDER": "1",
        "STUDIO_PUBLIC_APP_URL": "http://localhost",
        "STUDIO_API_BASE_URL": "http://localhost",
        "STUDIO_DATABASE_URL": studio_database,
        "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
        "STUDIO_CREDENTIAL_RATE_LIMIT_PER_MINUTE": "100000",
        "STUDIO_JOB_WORKERS": "0",
    }
    app = build_app(load_config(values), start_worker=False)
    jar = {"cookies": {}, "csrf": ""}

    def call(method, path, body=None):
        headers = {"host": "localhost", "accept": "application/json",
                   "origin": "http://localhost"}
        if body is not None:
            headers["content-type"] = "application/json"
        if jar["cookies"]:
            headers["cookie"] = "; ".join("%s=%s" % kv for kv in jar["cookies"].items())
        if jar["csrf"] and method != "GET":
            headers["x-csrf-token"] = jar["csrf"]
        payload = b"" if body is None else json.dumps(body).encode()
        response = handle(method=method, path=path, headers=headers, body=payload, app=app)
        for name, value in (response.headers or {}).items():
            if name.lower() == "set-cookie":
                for item in (value if isinstance(value, (list, tuple)) else [value]):
                    pair = str(item).split(";", 1)[0]
                    if "=" in pair:
                        key, _, val = pair.partition("=")
                        jar["cookies"][key.strip()] = val.strip()
        try:
            return response.status, json.loads(response.body or b"{}")
        except Exception:
            return response.status, {}

    # One account for the module; every test queues its own job.
    tag = uuid.uuid4().hex[:8]
    status, body = call("POST", "/api/auth/register", {
        "email": "adv-%s@example.test" % tag,
        "password": "advance-password-%s" % tag,
        "display_name": "Advance",
    })
    assert status in (200, 201), body
    jar["csrf"] = (body.get("session") or {}).get("csrf_token") or jar["cookies"].get("studio_csrf", "")

    status, body = call("POST", "/api/connections", {
        "name": "mock-" + tag, "provider_slug": "mock", "adapter_type": "mock",
        "base_url": "https://api.openai.com/v1", "auth_scheme": "none",
    })
    assert status in (200, 201), body
    connection = (body.get("connection") or body.get("data") or {})["id"]

    status, body = call("POST", "/api/connections/%s/models" % connection, {
        "model_key": "mock/placeholder-image", "display_name": "Mock image",
        "modality": "image", "modes": ["text_to_image"], "source": "manual",
    })
    assert status in (200, 201), body

    try:
        yield app, call, connection
    finally:
        app.shutdown()


def _queue(call, connection):
    status, body = call("POST", "/api/generations/images", {
        "model_key": "mock/placeholder-image",
        "connection_id": connection,
        "prompt": "a polled generation",
        "mode": "text_to_image",
    })
    assert status in (200, 201, 202), body
    return (body.get("job") or body.get("data") or {})["id"]


class TestShouldAdvance:
    def test_a_queued_job_should_be_advanced(self):
        assert should_advance({"status": "queued"}) is True

    def test_every_active_state_is_included(self):
        for state in ACTIVE_STATES:
            assert should_advance({"status": state}) is True, state

    def test_a_finished_job_is_left_alone(self):
        for state in ("completed", "failed", "cancelled", "timed_out"):
            assert should_advance({"status": state}) is False, state

    def test_an_unknown_state_is_left_alone(self):
        assert should_advance({"status": "something-new"}) is False


class TestAPollAdvancesTheJob:
    def test_polling_a_queued_job_drives_it_to_completion(self, host):
        """The property the deployment relies on.

        No cron, no worker thread: the job moves because somebody asked about it.
        The first poll submits, later polls pick up the provider's answer, and the
        job ends `completed` with an output and an asset.
        """
        app, call, connection = host
        job_id = _queue(call, connection)

        seen = []
        deadline = time.time() + 30
        final = ""
        while time.time() < deadline:
            status, body = call("GET", "/api/jobs/%s" % job_id)
            assert status == 200, body
            final = (body.get("job") or {}).get("status", "")
            if final not in seen:
                seen.append(final)
            if final in ("completed", "failed", "cancelled"):
                break
            # A poll claims and drives; the mock needs its delay to elapse before
            # it reports completion, so a real wait is part of the test.
            time.sleep(1.0)

        assert final == "completed", "job ended %r after polls; saw %s" % (final, seen)
        assert "queued" in seen or "submitting" in seen, seen

        row = app.db.query_dict(
            "SELECT status FROM generation_jobs WHERE id = ?", (job_id,))
        assert row["status"] == "completed"
        assert app.db.count("generation_outputs") >= 1
        assert app.db.count("assets") >= 1

    def test_polling_without_a_pending_job_is_harmless(self, host):
        """A poll on a finished job must not re-drive it or error."""
        app, call, connection = host
        job_id = _queue(call, connection)
        deadline = time.time() + 30
        while time.time() < deadline:
            status, body = call("GET", "/api/jobs/%s" % job_id)
            if (body.get("job") or {}).get("status") == "completed":
                break
            time.sleep(1.0)
        before = app.db.count("generation_outputs")
        for _ in range(3):
            status, body = call("GET", "/api/jobs/%s" % job_id)
            assert status == 200
            assert (body.get("job") or {}).get("status") == "completed"
        assert app.db.count("generation_outputs") == before, "a finished job was re-driven"
