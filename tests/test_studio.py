"""End-to-end tests for the Studio platform, over real HTTP.

These mount the same HttpApp the server uses on a real socket and speak real
HTTP to it, so what is under test is the deployed entry point rather than a
re-implementation of it. The provider is the local mock adapter, which is the
only adapter allowed to render an output without a network call; every test that
uses it asserts the mock flag travelled with the response.

Run:  python -m pytest tests/test_studio.py -q
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402

PASSWORD = "correct horse battery staple"


class Client:
    """A browser-shaped client: cookies, a CSRF header, and real HTTP."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def _cookie_header(self) -> str:
        return "; ".join("%s=%s" % item for item in self.cookies.items())

    def call(self, method: str, path: str, body=None, *, raw: bool = False):
        data = None
        headers = {"Accept": "application/json", "Origin": self.base}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = self._cookie_header()
        if self.csrf and method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=30)
            status, payload, cookies = response.status, response.read(), response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            status, payload, cookies = error.code, error.read(), error.headers.get_all("Set-Cookie") or []
        self._absorb(cookies)
        if raw:
            return status, payload
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:200].decode("utf-8", "replace")}

    def _absorb(self, cookies) -> None:
        for line in cookies:
            head = line.split(";")[0]
            name, _, value = head.partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]

    def register(self, email: str = ""):
        """Create an account and adopt its cookies. Returns (status, body).

        Each test needs its own account: the database is shared for the whole
        module, so a fixed address collides on the second test that registers.
        """
        email = email or "user-%s@example.com" % os.urandom(6).hex()
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {"email": email, "password": PASSWORD, "display_name": "Ada"},
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]
        return status, payload

    def mock_connection(self, name: str = "mock") -> dict:
        status, payload = self.call(
            "POST",
            "/api/connections",
            {
                "name": name,
                "adapter_type": "mock",
                "base_url": "http://127.0.0.1:9/mock",
                "auth_scheme": "none",
            },
        )
        assert status == 201, payload
        return payload["connection"]

    def mock_model(self, connection_id: str, model_key: str = "mock/placeholder-image") -> dict:
        status, payload = self.call(
            "POST",
            "/api/connections/%s/models" % connection_id,
            {
                "model_key": model_key,
                "display_name": "Mock image",
                "modality": "image",
                "modes": ["text_to_image"],
                "capabilities": {"max_outputs": 4},
            },
        )
        assert status == 201, payload
        return payload["model"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    """A whole platform: migrated database, local object store, live HTTP server."""
    data_dir = str(tmp_path_factory.mktemp("studio"))
    # The port has to be known before the app is built, because the origin
    # allowlist that guards state-changing requests is the configured public URL.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    base = "http://127.0.0.1:%d" % port
    config = load_config(
        {
            "STUDIO_DATA_DIR": data_dir,
            "STUDIO_ENV": "development",
            "STUDIO_ENCRYPTION_KEY": "a" * 64,
            "STUDIO_AUTH_SECRET": "b" * 40,
            "STUDIO_ENABLE_MOCK_PROVIDER": "1",
            "STUDIO_PUBLIC_APP_URL": base,
            "STUDIO_API_BASE_URL": base,
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_CREDENTIAL_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "0",
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound = "http://127.0.0.1:%d" % server.server_address[1]
    yield type("Studio", (), {"app": app, "base": bound, "data_dir": data_dir})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    """A fresh browser-shaped client for one test."""
    return Client(studio.base)


@pytest.fixture()
def ready(client):
    """A signed-in workspace with one mock connection and one mock model."""
    client.register("owner-%s@example.com" % os.urandom(4).hex())
    connection = client.mock_connection()
    model = client.mock_model(connection["id"])
    return client, connection, model


# ---------------------------------------------------------------------------
# Status and configuration
# ---------------------------------------------------------------------------


def test_status_reports_what_is_actually_wired(client):
    status, body = client.call("GET", "/api/system/status")
    assert status == 200
    described = body["status"]
    assert described["database"] == "ok"
    assert described["storage_backend"] in ("local", "s3")
    slugs = [item["slug"] for item in described["adapters"]]
    assert {"fal_queue", "openai_images", "declarative_http", "mock"} <= set(slugs)
    assert described["billing"]["mode"] == "byok"
    assert described["billing"]["platform_credits"] is False
    # The egress policy is reported without the addresses it blocks.
    assert "blocked_networks" not in described["egress"]
    assert described["egress"]["allow_insecure_http"] is False
    assert described["worker"]["running"] is False


def test_status_never_contains_a_secret(client, studio):
    _, body = client.call("GET", "/api/system/status")
    text = json.dumps(body)
    for secret in (studio.app.config.security.encryption_key, studio.app.config.security.auth_secret):
        assert secret and secret not in text


# ---------------------------------------------------------------------------
# Authentication and authorisation
# ---------------------------------------------------------------------------


def test_register_signs_in_and_issues_both_cookies(client):
    status, body = client.register()
    assert status == 201
    assert body["user"]["email"].startswith("user-")
    assert body["workspace"]["role"] == "owner"
    assert "studio_session" in client.cookies
    assert "studio_csrf" in client.cookies
    assert "password" not in json.dumps(body)


def test_me_returns_permissions_and_workspaces(client):
    client.register()
    status, body = client.call("GET", "/api/auth/me")
    assert status == 200
    assert body["user"]["email"].startswith("user-")
    assert "connection:manage" in body["permissions"]
    assert len(body["workspaces"]) == 1
    assert body["csrf_token"]


def test_unauthenticated_read_is_refused(client):
    status, body = client.call("GET", "/api/connections")
    assert status == 401
    assert body["error"]["code"] == "unauthenticated"
    assert body["error"]["request_id"]


def test_wrong_password_is_indistinguishable_from_unknown_account(client):
    _status, created = client.register()
    known = created["user"]["email"]
    _, wrong = client.call(
        "POST", "/api/auth/login", {"email": known, "password": "nope nope nope"}
    )
    _, missing = client.call(
        "POST", "/api/auth/login", {"email": "ghost@example.com", "password": "nope nope nope"}
    )
    assert wrong["error"]["message"] == missing["error"]["message"]
    assert wrong["error"]["code"] == missing["error"]["code"]


def test_state_changing_request_needs_a_csrf_token(client):
    client.register()
    client.csrf = ""
    status, body = client.call(
        "POST",
        "/api/connections",
        {"name": "x", "adapter_type": "mock", "base_url": "http://127.0.0.1:9/m", "auth_scheme": "none"},
    )
    assert status == 403
    assert body["error"]["code"] == "forbidden"


def test_foreign_origin_is_refused(client):
    client.register()
    request = urllib.request.Request(
        client.base + "/api/connections",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": "https://attacker.example",
            "Cookie": "; ".join("%s=%s" % item for item in client.cookies.items()),
            "X-CSRF-Token": client.csrf,
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=30)
    assert caught.value.code == 403

# ---------------------------------------------------------------------------
# BYOK connections and the credential envelope
# ---------------------------------------------------------------------------


def test_connection_types_come_from_the_live_registry(client):
    client.register()
    status, body = client.call("GET", "/api/connections/types")
    assert status == 200
    slugs = {item["slug"] for item in body["types"]}
    assert {"fal_queue", "openai_images", "declarative_http", "mock"} == slugs
    for item in body["types"]:
        assert "is_mock" in item and "discovers_models" in item


def test_key_is_sealed_and_never_returned(client, studio):
    client.register()
    secret = "sk-test-" + os.urandom(12).hex()
    status, body = client.call(
        "POST",
        "/api/connections",
        {
            "name": "openai",
            "adapter_type": "openai_images",
            "base_url": "https://api.openai.com/v1",
            "credential": secret,
        },
    )
    assert status == 201, body
    text = json.dumps(body)
    assert secret not in text
    assert secret[-4:] in text and len(secret) > 4
    credential = body["connection"]["credential"]
    assert credential["present"] is True
    assert credential["last4"] == secret[-4:]
    assert "credential_ciphertext" not in text

    row = studio.app.db.query_dict(
        "SELECT credential_ciphertext FROM api_connections WHERE id = ?",
        (body["connection"]["id"],),
    )
    stored = row["credential_ciphertext"]
    assert stored and stored.startswith("senc.")
    assert secret not in stored


def test_ssrf_metadata_address_is_refused(client):
    client.register()
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://127.0.0.1:6379/",
    ):
        status, body = client.call(
            "POST",
            "/api/connections",
            {
                "name": "probe",
                "adapter_type": "declarative_http",
                "base_url": url,
                "credential": "x",
                "mapping": {"submit": {"path": "/v1"}},
            },
        )
        assert status >= 400, url
        assert body["error"]["code"] in ("network_restricted", "validation_failed")


def test_plain_http_is_refused_for_a_credential_bearing_provider(client):
    client.register()
    status, body = client.call(
        "POST",
        "/api/connections",
        {
            "name": "insecure",
            "adapter_type": "openai_images",
            "base_url": "http://api.example.com/v1",
            "credential": "sk-x",
        },
    )
    assert status == 400
    assert body["error"]["code"] == "network_restricted"
    assert "https" in (body["error"]["hint"] or "").lower()


def test_unknown_adapter_type_is_named(client):
    client.register()
    status, body = client.call(
        "POST",
        "/api/connections",
        {"name": "x", "adapter_type": "not_a_provider", "base_url": "https://example.com"},
    )
    assert status >= 400
    assert body["error"]["field"] == "adapter_type"
    assert "not_a_provider" in body["error"]["message"]


def test_credential_header_cannot_be_set_by_the_client(client):
    client.register()
    status, body = client.call(
        "POST",
        "/api/connections",
        {
            "name": "sneaky",
            "adapter_type": "mock",
            "base_url": "http://127.0.0.1:9/m",
            "auth_scheme": "none",
            "extra_headers": {"Authorization": "Bearer nope"},
        },
    )
    assert status == 400
    assert body["error"]["field"] == "extra_headers"


def test_mock_connection_test_is_recorded_with_a_timestamp(ready):
    client, connection, _model = ready
    status, body = client.call("POST", "/api/connections/%s/test" % connection["id"])
    assert status == 200
    result = body["test"]
    assert result["ok"] is True
    assert result["checked_at"]
    assert result["detail"]["external_call"] is False
    assert result["detail"]["billable"] is False


def test_key_rotation_replaces_the_fingerprint(ready):
    client, connection, _model = ready
    _, before = client.call("GET", "/api/connections/%s" % connection["id"])
    status, body = client.call(
        "POST",
        "/api/connections/%s/rotate-key" % connection["id"],
        {"credential": "sk-rotated-" + os.urandom(8).hex()},
    )
    assert status == 200, body
    assert body["connection"]["credential"]["fingerprint"] != before["connection"]["credential"]["fingerprint"]
    assert body["connection"]["id"] == connection["id"]


def test_deleting_a_connection_scrubs_the_ciphertext(client, studio):
    client.register()
    _, created = client.call(
        "POST",
        "/api/connections",
        {
            "name": "openai",
            "adapter_type": "openai_images",
            "base_url": "https://api.openai.com/v1",
            "credential": "sk-delete-me",
        },
    )
    connection_id = created["connection"]["id"]
    status, _ = client.call("DELETE", "/api/connections/%s" % connection_id)
    assert status == 200
    row = studio.app.db.query_dict(
        "SELECT credential_ciphertext, credential_key_id, credential_last4 FROM api_connections WHERE id = ?",
        (connection_id,),
    )
    assert not row["credential_ciphertext"]
    assert not row["credential_key_id"]
    assert not row["credential_last4"]

# ---------------------------------------------------------------------------
# The model registry and capability honesty
# ---------------------------------------------------------------------------


def test_registered_model_carries_the_mock_flag(ready):
    _client, _connection, model = ready
    assert model["is_mock"] is True
    assert model["provider_slug"] == "mock"


def test_capabilities_list_every_mode_with_a_reason(ready):
    client, _connection, model = ready
    status, body = client.call("GET", "/api/models/%s/capabilities" % model["id"])
    assert status == 200
    modes = body["modes"]
    assert len(modes) == 15
    supported = {item["key"] for item in modes if item["supported"]}
    assert supported == {"text_to_image"}
    for item in modes:
        if not item["supported"]:
            assert item["reason"]


def test_camera_moves_state_whether_they_are_native(ready):
    client, _connection, model = ready
    _, body = client.call("GET", "/api/models/%s/capabilities" % model["id"])
    moves = body["camera_moves"]
    assert len(moves) == 21
    assert all(isinstance(item["is_native"], bool) for item in moves)
    assert any(item["is_native"] for item in moves)
    assert any(not item["is_native"] for item in moves)


def test_a_video_mode_on_an_image_model_is_refused(ready):
    """An image model cannot be handed a video mode, and nothing infers otherwise.

    The mode vocabulary is closed per modality. Registering text_to_video on an
    image model is refused with the known list, so the studio never renders a
    control the provider could not honour.
    """
    client, connection, _model = ready
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {"model_key": "mock/placeholder-image", "modality": "image", "modes": ["text_to_video"]},
    )
    assert status == 400
    assert body["error"]["code"] == "capability_unsupported"


def test_a_model_key_the_provider_cannot_render_is_refused_at_registration(ready):
    """A typo in the model key must fail where it was typed, not in the queue.

    The mock provider knows exactly three keys. Accepting any other one produced
    a queued job, a worker pass and a failed job minutes later, with a message
    about a model that does not exist -- long after the user could have fixed it.
    """
    client, connection, _model = ready
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {"model_key": "not-a-real-key", "modality": "image", "modes": ["text_to_image"]},
    )
    assert status == 400
    assert body["error"]["field"] == "model_key"
    assert "mock/placeholder-image" in (body["error"].get("hint") or "")


def test_parameter_schema_is_rendered_and_defaults_reported(ready):
    client, connection, _model = ready
    _status, created = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {
            "model_key": "mock/placeholder-edit",
            "display_name": "With parameters",
            "modality": "image",
            "modes": ["text_to_image"],
            "parameter_schema": {
                "parameters": [
                    {"name": "steps", "type": "integer", "label": "Steps", "default": 20, "minimum": 1, "maximum": 50},
                    {"name": "style", "type": "enum", "label": "Style", "options": ["natural", "vivid"]},
                ]
            },
            "defaults": {"style": "natural"},
        },
    )
    assert _status == 201, created
    model_id = created["model"]["id"]
    status, body = client.call("GET", "/api/models/%s/parameters" % model_id)
    assert status == 200
    names = [item["name"] for item in body["schema"]["parameters"]]
    assert names == ["steps", "style"]
    assert body["defaults"] == {"style": "natural"}


def test_malformed_parameter_schema_is_refused(ready):
    client, connection, _model = ready
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {
            "model_key": "mock/placeholder-image",
            "modality": "image",
            "modes": ["text_to_image"],
            "parameter_schema": {"parameters": [{"name": "x", "type": "python"}]},
        },
    )
    assert status == 400
    assert body["error"]["code"] == "validation_failed"


def test_model_list_filters_by_modality(ready, client):
    client2 = Client(client.base)
    client2.register("other-%s@example.com" % os.urandom(4).hex())
    connection = client2.mock_connection()
    client2.mock_model(connection["id"], "mock/placeholder-video")
    status, body = client2.call("GET", "/api/models?modality=video")
    assert status == 200
    assert body["models"] == []


# ---------------------------------------------------------------------------
# Generation, the worker, and stored output
# ---------------------------------------------------------------------------


def test_generation_queues_and_the_worker_completes_it(ready, studio):
    client, _connection, model = ready
    status, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "a lighthouse at dusk"},
    )
    assert status == 202, body
    job = body["job"]
    assert job["status"] == "queued"
    assert job["is_mock"] is True

    workspace_id = job["workspace_id"]
    job_id = job["id"]
    for _ in range(25):
        current = studio.app.repos.jobs.get(workspace_id, job_id)
        if current["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.3)
        studio.app.runner.drive(current)
    final = studio.app.repos.jobs.get(workspace_id, job_id)
    assert final["status"] == "completed", final.get("error_message")
    assert final["outputs"]
    assert final["outputs"][0]["asset_id"]


def test_a_mode_the_model_does_not_declare_is_refused_before_queueing(ready, studio):
    client, _connection, model = ready
    status, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "inpaint", "prompt": "x"},
    )
    assert status == 400
    assert body["error"]["code"] == "capability_unsupported"
    # Nothing was queued for this model. Earlier tests have their own jobs, so the
    # count is scoped rather than global.
    assert (
        studio.app.db.scalar(
            "SELECT COUNT(*) FROM generation_jobs WHERE model_id = ?", (model["id"],)
        )
        == 0
    )


def test_duplicate_submission_returns_the_same_job(ready):
    client, _connection, model = ready
    payload = {
        "model_id": model["id"],
        "mode": "text_to_image",
        "prompt": "same request twice",
        "client_request_id": "abc-123",
    }
    _s1, first = client.call("POST", "/api/generations/images", payload)
    _s2, second = client.call("POST", "/api/generations/images", payload)
    assert first["job"]["id"] == second["job"]["id"]
    assert second["deduplicated"] is True

def test_generated_asset_is_served_and_isolated(ready, studio):
    client, _connection, model = ready
    _status, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "asset isolation"},
    )
    job = body["job"]
    for _ in range(25):
        current = studio.app.repos.jobs.get(job["workspace_id"], job["id"])
        if current["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
        studio.app.runner.drive(current)
    final = studio.app.repos.jobs.get(job["workspace_id"], job["id"])
    assert final["status"] == "completed"
    asset_id = final["outputs"][0]["asset_id"]

    status, payload = client.call("GET", "/api/assets/%s/content" % asset_id, raw=True)
    assert status == 200
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"

    # Another workspace cannot read it, and is told nothing about whether it exists.
    stranger = Client(studio.base)
    stranger.register("stranger-%s@example.com" % os.urandom(4).hex())
    status, body = stranger.call("GET", "/api/assets/%s/content" % asset_id)
    assert status == 404
    assert body["error"]["code"] == "not_found"
    status, body = stranger.call("GET", "/api/jobs/%s" % job["id"])
    assert status == 404


def test_preexisting_asset_cannot_be_smuggled_in_a_second_workspace(ready, studio):
    client, _connection, _model = ready
    png = _tiny_png()
    status, body = client.call(
        "POST",
        "/api/assets",
        {"data_base64": base64.b64encode(png).decode(), "file_name": "dot.png", "content_type": "image/png"},
    )
    assert status == 201, body
    asset_id = body["asset"]["id"]

    stranger = Client(studio.base)
    stranger.register("thief-%s@example.com" % os.urandom(4).hex())
    status, body = stranger.call("GET", "/api/assets/%s" % asset_id)
    assert status == 404


def test_listing_assets_returns_paged_rows_and_a_cursor(ready):
    """Regression for the unpacking bug in routes/assets.py:97.

    The route expected ``(rows, cursor)`` from ``repos.assets.list(...)`` but the
    repository returned a plain list, so a workspace with more than one asset hit
    a ``ValueError: too many values to unpack (expected 2, got 3/4)`` and answered
    500. This test creates two assets in the same workspace and asks for the
    listing. If the unpacking regresses again, this test fails with the same
    500 the smoke run produced, before any other test can pass on top of the
    wrong return shape.
    """
    client, _connection, _model = ready
    png = _tiny_png()
    created_ids = []
    for index in ("one.png", "two.png"):
        status, body = client.call(
            "POST",
            "/api/assets",
            {
                "data_base64": base64.b64encode(png).decode(),
                "file_name": index,
                "content_type": "image/png",
            },
        )
        assert status == 201, body
        created_ids.append(body["asset"]["id"])
    assert len(set(created_ids)) == 2, "uploads must produce distinct asset ids"

    # Page 1: the default page is the only one that has to carry the bug-fixing
    # shape. The bug made this 500 with the same kind of asset pair that this
    # test builds, so just verifying status=200 here is enough to catch the
    # regression.
    status, body = client.call("GET", "/api/assets")
    assert status == 200, body
    assert isinstance(body.get("assets"), list)
    assert {item["id"] for item in body["assets"]} >= set(created_ids)
    # The response carries the documented pagination key.
    assert "next_cursor" in body, body

    # Filtering by kind and by query string both used to 500. They are the most
    # common shapes a UI sends, so cover them here even if they do not paginate.
    for params in ("?limit=1", "?kind=image", "?favorite=false", "?q=png&limit=5"):
        status, body = client.call("GET", "/api/assets" + params)
        assert status == 200, (params, body)
        assert isinstance(body.get("assets"), list)


def test_upload_proves_the_type_from_the_bytes(ready):
    client, _connection, _model = ready
    disguised = base64.b64encode(b"MZ\x90\x00\x03\x00\x00\x00").decode()
    status, body = client.call(
        "POST",
        "/api/assets",
        {"data_base64": disguised, "file_name": "notreally.png", "content_type": "image/png"},
    )
    assert status == 400
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field"] == "file"


def test_upload_refuses_malformed_base64(ready):
    client, _connection, _model = ready
    status, body = client.call(
        "POST", "/api/assets", {"data_base64": "not base64!!", "file_name": "x.png"}
    )
    assert status == 400
    assert body["error"]["field"] == "data_base64"


def test_asset_thumbnail_route_serves_something(ready):
    client, _connection, _model = ready
    png = _tiny_png()
    _s, created = client.call(
        "POST",
        "/api/assets",
        {"data_base64": base64.b64encode(png).decode(), "file_name": "dot.png", "content_type": "image/png"},
    )
    asset = created["asset"]
    assert asset["content_url"].endswith("/content")
    status, payload = client.call("GET", asset["thumbnail_url"] or asset["content_url"], raw=True)
    assert status == 200
    assert len(payload) > 50


# ---------------------------------------------------------------------------
# Jobs: history, cancel, retry
# ---------------------------------------------------------------------------


def test_job_history_records_each_state_change(ready, studio):
    client, _connection, model = ready
    _s, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "history"},
    )
    job = body["job"]
    for _ in range(25):
        current = studio.app.repos.jobs.get(job["workspace_id"], job["id"])
        if current["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
        studio.app.runner.drive(current)
    status, events = client.call("GET", "/api/jobs/%s/events" % job["id"])
    assert status == 200
    states = [item["status"] for item in events["events"] if item.get("status")]
    assert "queued" in states
    assert "completed" in states


def test_finished_job_cannot_be_cancelled(ready, studio):
    client, _connection, model = ready
    _s, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "cancel me"},
    )
    job = body["job"]
    for _ in range(25):
        current = studio.app.repos.jobs.get(job["workspace_id"], job["id"])
        if current["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
        studio.app.runner.drive(current)
    status, payload = client.call("POST", "/api/jobs/%s/cancel" % job["id"])
    assert status == 409
    assert payload["error"]["code"] == "conflict"


def test_queued_job_is_cancelled_without_contacting_the_provider(ready):
    client, _connection, model = ready
    _s, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "never sent"},
    )
    job = body["job"]
    status, payload = client.call("POST", "/api/jobs/%s/cancel" % job["id"])
    assert status == 200, payload
    assert payload["job"]["status"] == "cancelled"
    assert payload["job"]["provider_job_ref"] in (None, "")


def test_every_response_carries_a_request_id(ready):
    client, _connection, _model = ready
    _status, body = client.call("GET", "/api/nope")
    assert body["error"]["request_id"]


def test_wrong_verb_is_405_not_404(ready):
    client, _connection, _model = ready
    status, body = client.call("DELETE", "/api/models")
    assert status == 405


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


def test_preset_catalogue_is_served_with_categories(ready):
    client, _connection, _model = ready
    status, body = client.call("GET", "/api/presets?modality=image")
    assert status == 200
    assert body["builtin_count"] == 40
    assert len(body["categories"]) == 20
    status, body = client.call("GET", "/api/presets?modality=video")
    assert body["builtin_count"] == 30
    assert len(body["categories"]) == 17


def test_saved_prompt_round_trips(ready):
    client, _connection, _model = ready
    status, body = client.call(
        "POST",
        "/api/prompts",
        {"title": "Dusk", "body": "a lighthouse at dusk", "modality": "image", "tags": ["mood"]},
    )
    assert status == 201, body
    prompt_id = body["prompt"]["id"]
    status, listed = client.call("GET", "/api/prompts")
    assert status == 200
    assert [item["id"] for item in listed["prompts"]] == [prompt_id]
    status, _ = client.call("POST", "/api/prompts/%s/use" % prompt_id)
    assert status == 200


def test_usage_records_are_billed_to_the_user_provider(ready, studio):
    client, _connection, model = ready
    _s, body = client.call(
        "POST",
        "/api/generations/images",
        {"model_id": model["id"], "mode": "text_to_image", "prompt": "usage"},
    )
    job = body["job"]
    for _ in range(25):
        current = studio.app.repos.jobs.get(job["workspace_id"], job["id"])
        if current["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
        studio.app.runner.drive(current)
    status, payload = client.call("GET", "/api/usage")
    assert status == 200
    assert payload["usage"]
    assert payload["usage"][0]["billing_party"] == "user_provider"
    summary = payload["summary"]
    # No cost was reported by the local mock, so the summary must say the totals
    # are a lower bound rather than claim a zero invoice.
    assert summary["costs_are_partial"] is True
    assert summary["costs"] == []
    assert summary["by_billing_party"]["user_provider"]["records"] >= 1

# ---------------------------------------------------------------------------
# The prompt assistant
# ---------------------------------------------------------------------------


def test_assistant_rewrites_without_calling_anything(ready):
    client, _connection, _model = ready
    status, body = client.call(
        "POST",
        "/api/prompts/improve",
        {"prompt": "a lighthouse on a cliff, foggy morning", "operation": "expand"},
    )
    assert status == 200, body
    result = body["improvement"]
    assert result["provider"] == "local-rules"
    assert result["external_call"] is False
    # is_mock means "this is a placeholder standing in for a real model". This is
    # the feature itself, not a stand-in, so it must not be labelled a mock.
    assert result["is_mock"] is False
    assert result["changed"] is True
    assert result["notes"]
    assert result["original"] == "a lighthouse on a cliff, foggy morning"


def test_assistant_names_only_vocabulary_it_actually_added(ready):
    client, _connection, _model = ready
    _status, body = client.call(
        "POST", "/api/prompts/improve", {"prompt": "a fox in snow", "operation": "expand"}
    )
    result = body["improvement"]
    candidate = result["suggestion"]
    _s, caps = client.call("GET", "/api/assistant/capabilities")
    vocabulary = caps["assistant"]["vocabulary"]
    allowed = {word for words in vocabulary.values() for word in words}
    for line in candidate.splitlines()[1:]:
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        if label.strip().lower() == "quality":
            continue
        for word in [item.strip() for item in value.split(",")]:
            assert word in allowed, "assistant invented %r, which is not in its list" % word


def test_assistant_refuses_an_empty_prompt(ready):
    client, _connection, _model = ready
    status, body = client.call("POST", "/api/prompts/improve", {"prompt": "   "})
    assert status == 400
    assert body["error"]["field"] == "prompt"


def test_assistant_refuses_an_unknown_operation(ready):
    client, _connection, _model = ready
    status, body = client.call(
        "POST", "/api/prompts/improve", {"prompt": "x", "operation": "hallucinate"}
    )
    assert status == 400
    assert body["error"]["field"] == "operation"


def test_assistant_says_so_when_it_changed_nothing(ready):
    client, _connection, _model = ready
    _status, body = client.call(
        "POST", "/api/prompts/improve", {"prompt": "a single clause", "operation": "structure"}
    )
    result = body["improvement"]
    assert result["changed"] is False
    assert any("identical" in note for note in result["notes"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tiny_png() -> bytes:
    """A valid one-pixel PNG, built here so the test owns its own fixture."""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
