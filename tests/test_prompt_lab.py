"""The prompt layer as part of the studio, over real HTTP.

These tests exist because the layer dataset moved out of a standalone static page
and into the studio's own route table. The claims worth pinning are the ones a
merge can quietly break:

* the catalogue joins a *registered* model's endpoint back to the dataset, so the
  generate screen can say what the provider does to the prompt;
* an endpoint with no documented facts reports that absence, rather than looking
  like a model that adds nothing;
* preflight stays honest about unknown models instead of inventing defaults;
* every route needs a session, like the rest of the workspace data.

Run:  python -m pytest tests/test_prompt_lab.py -q
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
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
                "email": "layer-%s@example.com" % os.urandom(6).hex(),
                "password": PASSWORD,
                "display_name": "Ada",
            },
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("prompt-lab"))
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
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "0",
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


# ---------------------------------------------------------------------------


def test_the_routes_need_a_session(studio):
    anonymous = Client(studio.base)
    for method, path in (
        ("GET", "/api/prompt/models"),
        ("GET", "/api/prompt/models/seedream-v4"),
        ("GET", "/api/prompt/catalog"),
    ):
        status, body = anonymous.call(method, path)
        assert status == 401, (path, status, body)
        assert body["error"]["code"] == "unauthenticated"


def test_the_model_list_is_the_dataset_not_a_capability_claim(client):
    status, body = client.call("GET", "/api/prompt/models")
    assert status == 200, body
    models = body["models"]
    assert len(models) >= 15
    assert body["dataset_version"]
    by_slug = {item["slug"]: item for item in models}
    seedream = by_slug["seedream-v4"]
    assert seedream["kind"] == "image"
    assert seedream["layer_count"] == seedream["active_layer_count"] == 3


def test_a_model_detail_carries_its_sources(client):
    status, body = client.call("GET", "/api/prompt/models/seedream-v4")
    assert status == 200, body
    model = body["model"]
    assert model["endpoint"] == "fal-ai/bytedance/seedream/v4/text-to-image"
    assert model["layers"]
    assert model["sources"]
    assert all(layer["source"] for layer in model["layers"])


def test_an_undocumented_model_is_named_as_such(client):
    status, body = client.call("GET", "/api/prompt/models/not-a-real-model")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_the_catalog_joins_a_registered_endpoint_to_its_layers(client):
    status, body = client.call("GET", "/api/prompt/catalog")
    assert status == 200, body
    rows = body["endpoints"]
    assert len(rows) >= 20
    by_endpoint = {row["endpoint"]: row for row in rows}
    joined = by_endpoint["fal-ai/bytedance/seedream/v4/text-to-image"]
    assert joined["prompt_model"]["slug"] == "seedream-v4"
    assert joined["prompt_model"]["active_layer_count"] == 3
    assert joined["modality"] == "image"
    assert body["documented"] + body["undocumented"] == len(rows)


def test_an_endpoint_without_documented_layers_says_so(client):
    """`prompt_model: None` is the absence of facts, and must not read as safe."""
    status, body = client.call("GET", "/api/prompt/catalog")
    assert status == 200
    undocumented = [row for row in body["endpoints"] if row["prompt_model"] is None]
    assert undocumented, "the catalogue claims every endpoint is documented; check the join"
    assert all(row["endpoint"] for row in undocumented)


def test_the_catalog_filters_by_modality(client):
    status, body = client.call("GET", "/api/prompt/catalog?modality=video")
    assert status == 200
    assert body["endpoints"]
    assert {row["modality"] for row in body["endpoints"]} == {"video"}


def test_preflight_reports_every_active_layer_and_its_off_switch(client):
    status, body = client.call(
        "POST",
        "/api/prompt/preflight",
        {"model": "seedream-v4", "prompt": "A lighthouse at dusk, 35mm"},
    )
    assert status == 200, body
    result = body["preflight"]
    assert result["known_model"] is True
    assert result["unknown_model"] is False
    active = [layer for layer in result["layers"] if layer["active"]]
    assert active
    assert any(layer["kind"] in ("rewrite", "expand", "augment") for layer in active)
    assert result["warnings"]
    assert result["params"]["enhance_prompt_mode"]["source"] == "provider default"


def test_an_override_moves_a_parameter_to_your_column(client):
    status, body = client.call(
        "POST",
        "/api/prompt/preflight",
        {
            "model": "veo3-fast",
            "prompt": "A wide shot of a river at dawn",
            "options": {"auto_fix": False},
        },
    )
    assert status == 200, body
    params = body["preflight"]["params"]
    assert params["auto_fix"]["source"] == "your override"
    assert params["auto_fix"]["value"] is False


def test_an_unknown_model_gets_generic_layers_and_says_it_is_unknown(client):
    status, body = client.call(
        "POST",
        "/api/prompt/preflight",
        {"model": "some-vendor/not-in-the-dataset", "prompt": "A test prompt"},
    )
    assert status == 200, body
    result = body["preflight"]
    assert result["known_model"] is False
    assert result["unknown_model"] is True
    assert result["layers"], "an unknown model still gets the generic layers"
    assert any("No layer facts" in warning for warning in result["warnings"])


def test_a_prompt_is_required(client):
    status, body = client.call("POST", "/api/prompt/preflight", {"model": "seedream-v4"})
    assert status == 400
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field"] == "prompt"


def test_an_oversized_prompt_is_refused_by_name(client):
    status, body = client.call(
        "POST",
        "/api/prompt/preflight",
        {"model": "seedream-v4", "prompt": "x" * 20_001},
    )
    assert status == 400
    assert body["error"]["field"] == "prompt"


def test_options_must_be_an_object(client):
    status, body = client.call(
        "POST",
        "/api/prompt/preflight",
        {"model": "seedream-v4", "prompt": "ok", "options": "auto_fix=false"},
    )
    assert status == 400
    assert body["error"]["field"] == "options"
