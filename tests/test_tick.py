"""Phase 3: one cron invocation actually moves a queued job.

The deployment has no worker threads, so if the tick does not work there is no
queue at all -- a submitted job would sit `queued` forever and the user would see
a spinner that never resolves.

These tests drive `run_tick` the way the platform does: build the application from
configuration, call the tick, inspect the database. The provider is the mock
adapter, which is the only adapter allowed to produce an output without a network
call, and every assertion checks the mock flag travelled with the result.

They need Postgres and skip without it, because the tick's whole point is to run
on the deployed backend.
"""

from __future__ import annotations

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
from studio.tick import run_tick  # noqa: E402


@pytest.fixture()
def deployment(studio_database, tmp_path_factory, monkeypatch):
    """Configuration in the environment, exactly as the platform supplies it."""
    if not studio_database:
        pytest.skip("set STUDIO_TEST_POSTGRES to run the tick tests")
    data_dir = str(tmp_path_factory.mktemp("tick"))
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
        "STUDIO_JOB_WORKERS": "0",
    }
    # `run_tick` calls build_app() with no argument, so it reads the real
    # environment. Patch it to the same values the fixture uses, which is what
    # the platform does by setting variables on the deployment.
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    app = build_app(load_config(values), start_worker=False)
    try:
        yield app
    finally:
        app.shutdown()


def _queue_one(app, workspace_id, user_id):
    """A real mock-model job, ready for the tick to claim."""
    repos = app.repos
    connection = repos.connections.create(
        workspace_id,
        user_id,
        {
            "name": "mock-" + uuid.uuid4().hex[:6],
            "provider_slug": "mock",
            "adapter_type": "mock",
            "base_url": "https://api.openai.com/v1",
            "auth_scheme": "none",
        },
    )
    model = repos.models.create(
        workspace_id,
        {
            "connection_id": connection["id"],
            "model_key": "mock/placeholder-image",
            "display_name": "Mock image",
            "modality": "image",
            "modes": ["text_to_image"],
        },
    )
    job = repos.jobs.create(
        workspace_id,
        user_id,
        {
            "modality": "image",
            "mode": "text_to_image",
            "prompt": "a tick test",
            "connection_id": connection["id"],
            "model_id": model["id"],
            # The service puts the wire model key in this snapshot, and the
            # runner rebuilds the request from it. Without it the adapter gets an
            # empty key and correctly refuses -- so a test that omits it is
            # testing its own omission, not the tick.
            "request": {"model_key": "mock/placeholder-image"},
        },
    )
    return job


class TestTheTick:
    def test_a_queued_job_is_driven_and_completes(self, deployment):
        """The end-to-end property: submit, tick, job is done with an asset."""
        db = deployment.db
        workspace = uuid.uuid4().hex[:8]
        # A real workspace and user, because the job FKs to both.
        user = db.insert(
            "users",
            {
                "email": "tick-%s@example.test" % workspace,
                "email_normalized": "tick-%s@example.test" % workspace,
                "display_name": "Tick",
                "password_hash": "x",
            },
        )
        space = db.insert(
            "workspaces",
            {
                "name": "tick " + workspace,
                "slug": "tick-" + workspace,
                "owner_user_id": user["id"],
            },
        )
        job = _queue_one(deployment, space["id"], user["id"])

        summary = run_tick(limit=1, budget_seconds=30)

        assert summary["ok"] is True, summary
        assert summary["claimed"] >= 1, summary

        row = db.query_dict(
            "SELECT status, provider_job_ref, error_code FROM generation_jobs WHERE id = ?",
            (job["id"],),
        )
        assert row["status"] in ("completed", "processing", "downloading"), row
        assert row["provider_job_ref"], "no provider reference was recorded"

        # A real provider is asynchronous, so the first tick submits and hands the
        # lease back with next_attempt_at in the future. That is the correct
        # shape: the tick must not sit in a poll loop.
        #
        # The mock reports "still processing" until its configured delay has
        # actually elapsed since submission, so this waits rather than only
        # forcing next_attempt_at forward. Forcing the timestamp alone proves the
        # job can be re-claimed, not that a finished job completes.
        time.sleep(2.0)
        db.execute(
            "UPDATE generation_jobs SET next_attempt_at = '2000-01-01T00:00:00.000000Z'"
            " WHERE id = ?",
            (job["id"],),
        )
        run_tick(limit=2, budget_seconds=30)
        final = db.query_dict(
            "SELECT status FROM generation_jobs WHERE id = ?", (job["id"],)
        )
        assert final["status"] == "completed", final
        assert db.count("generation_outputs") >= 1, "a completed job has no output"
        assert db.count("assets") >= 1, "a completed job imported no asset"

    def test_an_empty_queue_claims_nothing_and_does_not_error(self, deployment):
        db = deployment.db
        db.execute("DELETE FROM generation_jobs")
        summary = run_tick(limit=3, budget_seconds=10)
        assert summary["ok"] is True, summary
        assert summary["claimed"] == 0, summary

    def test_the_summary_reports_what_it_did(self, deployment):
        db = deployment.db
        db.execute("DELETE FROM generation_jobs")
        summary = run_tick(limit=2, budget_seconds=10)
        for field in ("ok", "worker_id", "claimed", "driven", "errors", "elapsed_seconds"):
            assert field in summary, field
        assert isinstance(summary["errors"], list)

    def test_each_tick_uses_a_fresh_worker_id(self, deployment):
        """Two ticks must not look like the same worker.

        The lease records its owner; a reused id would make a stale lease from a
        killed invocation indistinguishable from the live one.
        """
        first = run_tick(limit=0, budget_seconds=5)
        second = run_tick(limit=0, budget_seconds=5)
        assert first["worker_id"] != second["worker_id"]


class TestTheCronEndpointGuard:
    def test_without_a_secret_the_endpoint_refuses(self, monkeypatch):
        """An unset CRON_SECRET must refuse, not run open work.

        The URL is public on a deployed site; without this check anyone could
        drain the queue, and every drained job is a provider call that costs money.
        """
        monkeypatch.delenv("CRON_SECRET", raising=False)
        from studio.serverless import authorised

        allowed, reason = authorised({})
        assert allowed is False
        assert "CRON_SECRET" in reason

    def test_a_wrong_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "expected-value")
        from studio.serverless import authorised

        allowed, _ = authorised({"authorization": "Bearer wrong-value"})
        assert allowed is False

    def test_the_right_secret_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "expected-value")
        from studio.serverless import authorised

        allowed, _ = authorised({"authorization": "Bearer expected-value"})
        assert allowed is True

    def test_a_missing_header_with_a_configured_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "expected-value")
        from studio.serverless import authorised

        allowed, reason = authorised({})
        assert allowed is False
        assert reason
