"""The Higgsfield-shaped loop, server-side: preset -> model -> job -> artifact.

This is the claim the product rests on. A preset is a look — a prompt template, an
aspect ratio and the settings that go with it — and one click should turn it into a
generation on a model the user brought a key for.

Two things used to break exactly here, and both are pinned below:

* A catalogue preset arrived without an `id` and with the dataset's own
  `negative_template` key, so the client could not select it reliably and the
  negative prompt never filled. `GET /api/presets` now returns one shape for both
  catalogue and saved presets.
* A preset's settings are not automatically settings the *model* declares. The
  backend refuses a parameter the model does not declare — correctly, because a
  silently dropped control is worse than a refusal — so the client filters a
  preset against the model's schema. The last test asserts the refusal, because
  that is what makes the filter necessary rather than decorative.

Run:  python -m pytest tests/test_preset_loop.py -q
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


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None):
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
            status, payload, cookies = response.status, response.read(), response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            status, payload, cookies = error.code, error.read(), error.headers.get_all("Set-Cookie") or []
        for line in cookies:
            name, _, value = line.split(";")[0].partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:200].decode("utf-8", "replace")}

    def register(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {
                "email": "loop-%s@example.com" % os.urandom(6).hex(),
                "password": PASSWORD,
                "display_name": "Ada",
            },
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("preset-loop"))
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
            "STUDIO_PUBLIC_APP_URL": base,
            "STUDIO_API_BASE_URL": base,
            "STUDIO_ENABLE_MOCK_PROVIDER": "1",
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "1",
            "STUDIO_DATABASE_URL": studio_database,
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type("Studio", (), {"app": app, "base": "http://127.0.0.1:%d" % server.server_address[1]})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    client.register()
    return client


def _mock_model(client, *, schema=None) -> dict:
    status, body = client.call(
        "POST",
        "/api/connections",
        {
            "name": "my key",
            "adapter_type": "mock",
            "base_url": "http://127.0.0.1:9/mock",
            "auth_scheme": "none",
        },
    )
    assert status == 201, body
    connection = body["connection"]
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {
            "model_key": "mock/placeholder-image",
            "display_name": "Placeholder image",
            "modality": "image",
            "modes": ["text_to_image", "image_to_image"],
            "capabilities": {
                "max_outputs": 4,
                "aspect_ratios": ["1:1", "4:5"],
                "supports_negative_prompt": True,
                "supports_seed": True,
            },
            "parameter_schema": schema or {"parameters": []},
        },
    )
    assert status == 201, body
    return body["model"]


# ---------------------------------------------------------------------------


def test_a_catalogue_preset_arrives_in_the_same_shape_as_a_saved_one(client):
    status, body = client.call("GET", "/api/presets?modality=image")
    assert status == 200, body
    assert body["builtin_count"] >= 30
    preset = body["presets"][0]
    assert preset["id"].startswith("builtin:")
    assert preset["is_builtin"] is True
    assert preset["prompt_template"]
    # The dataset calls it negative_template; the client and the saved-preset
    # shape call it negative_prompt_template. One name reaches the client.
    assert preset["negative_prompt_template"]
    assert "negative_template" not in preset
    assert preset["created_at"] is None
    assert preset["workspace_id"] is None


def test_a_builtin_preset_is_addressable_by_its_id(client):
    _, body = client.call("GET", "/api/presets?modality=image")
    preset = body["presets"][0]
    status, fetched = client.call("GET", "/api/presets/%s" % preset["id"])
    assert status == 200, fetched
    assert fetched["preset"]["id"] == preset["id"]
    assert fetched["preset"]["name"] == preset["name"]


def test_video_presets_are_normalised_too(client):
    status, body = client.call("GET", "/api/presets?modality=video")
    assert status == 200
    assert body["builtin_count"] >= 20
    assert all(item["id"].startswith("builtin:") for item in body["presets"])


def test_one_click_from_a_preset_produces_an_artifact(client):
    """Preset -> filtered settings -> generation -> job -> stored output."""
    model = _mock_model(
        client,
        schema={
            "parameters": [
                {"name": "steps", "type": "integer", "label": "Steps", "default": 20},
                {"name": "cfg_scale", "type": "number", "label": "CFG", "default": 7},
            ]
        },
    )
    _, presets = client.call("GET", "/api/presets?modality=image")
    preset = next(item for item in presets["presets"] if "portrait" in item["name"].lower())

    status, parameters = client.call("GET", "/api/models/%s/parameters" % model["id"])
    assert status == 200, parameters
    declared = {spec["name"] for spec in parameters["schema"]["parameters"]}
    defaults = dict(preset["defaults"] or {})
    # `aspect_ratio` rides in `defaults` because it is part of the look, not a
    # model parameter; the studio maps it to the frame control.
    frame = defaults.pop("aspect_ratio", None)
    kept = {name: value for name, value in defaults.items() if name in declared}
    assert kept, "the preset's settings and the model's schema do not overlap; the fixture is wrong"

    status, created = client.call(
        "POST",
        "/api/generations/images",
        {
            "model_id": model["id"],
            "mode": "text_to_image",
            "prompt": preset["prompt_template"].replace("{subject}", "a woman in her thirties"),
            "negative_prompt": preset["negative_prompt_template"],
            "count": 1,
            "aspect_ratio": frame or "1:1",
            "parameters": kept,
            "preset_id": preset["id"],
            "client_request_id": "req_%s" % os.urandom(5).hex(),
        },
    )
    assert status in (200, 201, 202), created
    job = created["job"]
    assert job["status"] == "queued"

    # The wire field is `status`, and "completed" is the terminal success state.
    state = job["status"]
    body = created
    for _ in range(80):
        if state in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.25)
        status, body = client.call("GET", "/api/jobs/%s" % job["id"])
        assert status == 200, body
        state = body["job"]["status"]

    assert state == "completed", body
    outputs = body["job"]["outputs"]
    assert outputs, "a completed job must carry its output"
    assert outputs[0]["asset_id"]
    # The placeholder provider is labelled everywhere it appears, including here.
    assert body["job"]["is_mock"] is True

    status, raw = client.call("GET", "/api/assets/%s/content" % outputs[0]["asset_id"])
    assert status == 200
    assert raw, "the stored artifact must be readable"


def test_a_parameter_the_model_does_not_declare_is_refused(client):
    """Why the client filters: the backend will not silently drop a setting."""
    model = _mock_model(client)
    status, body = client.call(
        "POST",
        "/api/generations/images",
        {
            "model_id": model["id"],
            "mode": "text_to_image",
            "prompt": "A lighthouse",
            "count": 1,
            "parameters": {"steps": 30, "sampler": "dpmpp_2m"},
            "client_request_id": "req_%s" % os.urandom(5).hex(),
        },
    )
    assert status == 400, body
    assert body["error"]["code"] == "validation_failed"
    assert "steps" in body["error"]["message"] or "sampler" in body["error"]["message"]
