"""Character references, and casting a character into a generation.

The reference route was answered with `404 That asset could not be found` for every
request, whatever the asset was: the handler passed one mapping to a repository
method whose third positional argument is the asset id, so the existence check
looked for the string `"{'asset_id': ...}"`. A character could therefore hold a
description and a list of references in the database, but no reference could ever
be attached through the API — which is the whole point of a character.

These tests hold the route open, and check that a curated reference travels into a
generation request as a request-level reference, which is how a face survives
across generations on a platform with no training.

Run:  python -m pytest tests/test_character_casting.py -q
"""

from __future__ import annotations

import base64
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
PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Origin": self.base}
        if data is not None:
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


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("casting"))
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
            "STUDIO_JOB_WORKERS": "0",
            "STUDIO_DATABASE_URL": studio_database,
        }
    )
    app = build_app(config)
    server = build_server(app.http, host="127.0.0.1", port=port, static_root="")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type("Studio", (), {"base": "http://127.0.0.1:%d" % server.server_address[1]})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "cast-%s@example.com" % os.urandom(5).hex(), "password": PASSWORD, "display_name": "Ada"},
    )
    assert status == 201, body
    if body.get("session", {}).get("csrf_token"):
        client.csrf = body["session"]["csrf_token"]
    return client


def _asset(client) -> str:
    status, body = client.call(
        "POST",
        "/api/assets",
        {"file_name": "face.png", "content_type": "image/png", "data_base64": PNG},
    )
    assert status == 201, body
    return body["asset"]["id"]


def _character(client) -> str:
    status, body = client.call(
        "POST",
        "/api/characters",
        {"name": "Ada", "description": "a woman in her thirties, red coat"},
    )
    assert status == 201, body
    return body["character"]["id"]


def _model(client) -> str:
    status, body = client.call(
        "POST",
        "/api/connections",
        {"name": "k", "adapter_type": "mock", "base_url": "http://127.0.0.1:9/mock", "auth_scheme": "none"},
    )
    assert status == 201, body
    connection_id = body["connection"]["id"]
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % connection_id,
        {
            "model_key": "mock/placeholder-image",
            "display_name": "Placeholder image",
            "modality": "image",
            "modes": ["text_to_image"],
            "capabilities": {"max_outputs": 2},
        },
    )
    assert status == 201, body
    return body["model"]["id"]


# ---------------------------------------------------------------------------


def test_a_reference_image_can_be_attached_to_a_character(client):
    character = _character(client)
    asset = _asset(client)
    status, body = client.call(
        "POST", "/api/characters/%s/references" % character, {"asset_id": asset, "role": "character_identity"}
    )
    assert status == 201, body
    assert body["reference"]["asset_id"] == asset
    assert body["reference"]["role"] == "character_identity"

    status, body = client.call("GET", "/api/characters/%s" % character)
    assert status == 200, body
    assert body["character"]["reference_count"] == 1
    assert [item["asset_id"] for item in body["character"]["references"]] == [asset]


def test_a_reference_to_someone_elses_asset_is_refused_by_name(client):
    character = _character(client)
    status, body = client.call(
        "POST", "/api/characters/%s/references" % character, {"asset_id": "not-an-asset"}
    )
    assert status == 404, body
    assert body["error"]["code"] == "not_found"


def test_attaching_the_same_reference_twice_is_a_conflict(client):
    character = _character(client)
    asset = _asset(client)
    first = client.call("POST", "/api/characters/%s/references" % character, {"asset_id": asset})
    assert first[0] == 201, first[1]
    status, body = client.call("POST", "/api/characters/%s/references" % character, {"asset_id": asset})
    assert status == 409, body
    assert body["error"]["code"] == "conflict"


def test_a_characters_reference_travels_into_a_generation(client):
    """Casting: the same reference the character holds is attached to the request."""
    character = _character(client)
    asset = _asset(client)
    client.call("POST", "/api/characters/%s/references" % character, {"asset_id": asset})
    model = _model(client)

    status, body = client.call("GET", "/api/characters/%s" % character)
    assert status == 200, body
    references = [
        {"asset_id": item["asset_id"], "role": "character_identity"}
        for item in body["character"]["references"]
    ]

    status, queued = client.call(
        "POST",
        "/api/generations/images",
        {
            "model_id": model,
            "mode": "text_to_image",
            "prompt": "a woman in her thirties, red coat, crossing a wet street",
            "count": 1,
            "references": references,
            "client_request_id": "req_%s" % os.urandom(5).hex(),
        },
    )
    assert status in (200, 201, 202), queued
    job = queued["job"]
    assert job["status"] == "queued"
    assert [item["asset_id"] for item in job["references"]] == [asset]
