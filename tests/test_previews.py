"""Rendered preset previews: a real frame, on the user's own key.

The claims pinned here are the ones that keep a BYO-key gallery honest:

* a preset with no render reports no preview -- it does not borrow the style card
  and call it a photograph;
* rendering requires a connected model, because it spends real money;
* the render is an ordinary job carrying `preset_key`, so it runs through the
  queue, records its status and its failure, and can be read back by the same
  routes as any other generation;
* a preview is workspace-scoped: one tenant's render never appears on another
  tenant's wall.

Run:  python -m pytest tests/test_previews.py -q
"""

from __future__ import annotations

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
BUILTIN = "builtin:image.portraiture.soft-window-light"


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, *, raw: bool = False):
        data = None
        headers = {"Accept": "application/json", "Origin": self.base}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join("%s=%s" % item for item in self.cookies.items())
        if self.csrf and method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=30)
            status, payload = response.status, response.read()
            cookies = response.headers.get_all("Set-Cookie") or []
            content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
            cookies = error.headers.get_all("Set-Cookie") or []
            content_type = error.headers.get("Content-Type", "")
        for line in cookies:
            name, _, value = line.split(";")[0].partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]
        if raw:
            return status, payload, content_type
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:200].decode("utf-8", "replace")}

    def register(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {
                "email": "preview-%s@example.com" % os.urandom(6).hex(),
                "password": PASSWORD,
                "display_name": "Ada",
            },
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]

    def mock_model(self, modality: str = "image") -> dict:
        status, payload = self.call(
            "POST",
            "/api/connections",
            {
                "name": "mock-%s" % os.urandom(3).hex(),
                "adapter_type": "mock",
                "base_url": "http://127.0.0.1:9/mock",
                "auth_scheme": "none",
            },
        )
        assert status == 201, payload
        connection = payload["connection"]
        mode = "text_to_image" if modality == "image" else "text_to_video"
        status, payload = self.call(
            "POST",
            "/api/connections/%s/models" % connection["id"],
            {
                "model_key": "mock/placeholder-%s" % modality,
                "display_name": "Mock %s" % modality,
                "modality": modality,
                "modes": [mode],
                "capabilities": {"max_outputs": 1, "aspect_ratios": ["4:5"]},
            },
        )
        assert status == 201, payload
        return payload["model"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("previews"))
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
            "STUDIO_JOB_WORKERS": "0",
            "STUDIO_DATABASE_URL": studio_database,
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type("Studio", (), {"app": app, "base": base})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    client.register()
    return client


def _drive(studio, workspace_id: str, job_id: str) -> None:
    """Run the queue's own worker step until the job is terminal.

    The mock adapter is asynchronous on purpose, so one pass submits and the next
    polls; this is the same loop the background worker runs, just in the test.
    """
    for _ in range(30):
        current = studio.app.repos.jobs.get(workspace_id, job_id)
        if current["status"] in ("completed", "failed", "cancelled"):
            return
        studio.app.runner.drive(current)
        time.sleep(0.1)
    final = studio.app.repos.jobs.get(workspace_id, job_id)
    raise AssertionError("the preview job did not reach a terminal state: %s" % final["status"])


def test_a_preset_with_no_render_reports_no_preview(client):
    status, body = client.call("GET", "/api/presets")
    assert status == 200, body
    assert body["presets"], "the catalogue has presets"
    for preset in body["presets"]:
        assert preset["preview"] is None, preset["id"]


def test_rendering_requires_a_connected_model(client):
    status, body = client.call("POST", "/api/presets/%s/preview" % BUILTIN, {})
    assert status == 400, body
    assert body["error"]["field"] == "model_id"
    assert "your own key" in body["error"]["hint"].lower()


def test_a_model_of_the_wrong_modality_is_refused(client):
    video = client.mock_model("video")
    status, body = client.call(
        "POST", "/api/presets/%s/preview" % BUILTIN, {"model_id": video["id"]}
    )
    assert status == 400, body
    assert body["error"]["field"] == "model_id"
    assert "image" in body["error"]["message"].lower()


def test_a_render_is_an_ordinary_job_and_becomes_the_tile(client, studio):
    model = client.mock_model("image")
    status, body = client.call(
        "POST", "/api/presets/%s/preview" % BUILTIN, {"model_id": model["id"]}
    )
    assert status == 202, body
    job = body["job"]
    assert job["preset_key"] == BUILTIN
    assert job["status"] == "queued"

    # Before the worker touches it, the preset reports the queued render rather
    # than hiding it behind an older absence.
    status, listed = client.call("GET", "/api/presets?modality=image")
    tile = next(item for item in listed["presets"] if item["id"] == BUILTIN)
    assert tile["preview"]["status"] == "queued"
    assert tile["preview"]["url"] is None

    _drive(studio, job["workspace_id"], job["id"])

    status, listed = client.call("GET", "/api/presets?modality=image")
    tile = next(item for item in listed["presets"] if item["id"] == BUILTIN)
    preview = tile["preview"]
    assert preview["status"] == "completed", preview
    assert preview["asset_id"], preview
    assert preview["url"].startswith("/api/assets/")
    assert preview["thumbnail_url"].endswith("/thumbnail")

    # The bytes really exist behind the URL, through the same asset route.
    status, payload, content_type = client.call("GET", preview["thumbnail_url"], raw=True)
    assert status == 200, payload[:120]
    assert content_type.startswith("image/")


def test_the_previews_route_lists_every_attempt_newest_first(client, studio):
    model = client.mock_model("image")
    status, body = client.call(
        "POST", "/api/presets/%s/preview" % BUILTIN, {"model_id": model["id"]}
    )
    assert status == 202, body
    _drive(studio, body["job"]["workspace_id"], body["job"]["id"])
    status, body = client.call(
        "POST", "/api/presets/%s/preview" % BUILTIN, {"model_id": model["id"]}
    )
    assert status == 202, body

    status, listed = client.call("GET", "/api/presets/%s/previews" % BUILTIN)
    assert status == 200, listed
    assert len(listed["previews"]) == 2
    assert listed["previews"][0]["created_at"] >= listed["previews"][1]["created_at"]


def test_the_batch_route_answers_only_for_the_keys_it_was_given(client, studio):
    model = client.mock_model("image")
    status, body = client.call(
        "POST", "/api/presets/%s/preview" % BUILTIN, {"model_id": model["id"]}
    )
    assert status == 202, body
    _drive(studio, body["job"]["workspace_id"], body["job"]["id"])

    status, body = client.call("GET", "/api/previews?keys=%s,builtin:nope" % BUILTIN)
    assert status == 200, body
    # The unknown key is simply absent; it is not invented as an empty preview.
    assert "builtin:nope" not in body["previews"]
    assert BUILTIN in body["previews"]
    assert body["previews"][BUILTIN]["status"] == "completed"


def test_a_second_workspace_sees_none_of_the_first_workspaces_renders(client):
    other = Client(client.base)
    other.register()
    status, body = other.call("GET", "/api/presets")
    assert status == 200, body
    for preset in body["presets"]:
        assert preset["preview"] is None, preset["id"]
