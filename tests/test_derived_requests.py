"""Derived requests: an edit, an extension or a transform starts from an output.

Two defects lived here, and both were invisible until a video mode was tried:

1. A reference never carried its `kind` (image or video). The validator refuses a
   mode that needs a source video unless one of the references *is* a video, so
   `extend`, `video_to_video`, `restyle` and `motion_transfer` could never be
   queued — for any provider — even with a video attached.
2. The derived routes copied only the parent's *input* references, never the
   output the parent produced. Deriving from a job that had just finished with a
   video therefore had no video to derive from.

The first is checked here through the HTTP surface. The second is checked against
the service directly, because building a parent job whose output is a real video
needs a provider that makes videos — the placeholder adapter produces a GIF even
for a video model, and it says so.

Run:  python -m pytest tests/test_derived_requests.py -q
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

#: The smallest thing that is unambiguously an MP4: one ftyp box.
MP4 = base64.b64encode(
    b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    b"\x00\x00\x00\x08free"
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
    data_dir = str(tmp_path_factory.mktemp("derived"))
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
    yield type(
        "Studio",
        (),
        {"app": app, "base": "http://127.0.0.1:%d" % server.server_address[1]},
    )
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "derived-%s@example.com" % os.urandom(5).hex(), "password": PASSWORD, "display_name": "Ada"},
    )
    assert status == 201, body
    if body.get("session", {}).get("csrf_token"):
        client.csrf = body["session"]["csrf_token"]
    return client


def _video_model(client) -> str:
    status, body = client.call(
        "POST",
        "/api/connections",
        {"name": "k", "adapter_type": "mock", "base_url": "http://127.0.0.1:9/mock", "auth_scheme": "none"},
    )
    assert status == 201, body
    status, body = client.call(
        "POST",
        "/api/connections/%s/models" % body["connection"]["id"],
        {
            "model_key": "mock/placeholder-video",
            "display_name": "Placeholder video",
            "modality": "video",
            "modes": ["text_to_video", "extend", "restyle"],
            "capabilities": {"max_outputs": 1, "durations_seconds": [4]},
        },
    )
    assert status == 201, body
    return body["model"]["id"]


def _upload(client, *, name: str, content_type: str, data: str) -> str:
    status, body = client.call(
        "POST", "/api/assets", {"file_name": name, "content_type": content_type, "data_base64": data}
    )
    assert status == 201, body
    return body["asset"]["id"]


def test_a_video_reference_makes_a_video_mode_reachable(client):
    """The kind travels with the reference; without it the mode is refused."""
    model = _video_model(client)
    video = _upload(client, name="clip.mp4", content_type="video/mp4", data=MP4)
    status, body = client.call(
        "POST",
        "/api/generations/videos",
        {
            "model_id": model,
            "mode": "extend",
            "prompt": "continue the shot",
            "count": 1,
            "duration_seconds": 4,
            "references": [{"asset_id": video, "role": "reference_video"}],
            "client_request_id": "req_%s" % os.urandom(5).hex(),
        },
    )
    assert status in (200, 201, 202), body
    assert [item["role"] for item in body["job"]["references"]] == ["reference_video"]


def test_an_image_reference_does_not_satisfy_a_video_mode(client):
    """The same request with an image is refused, and says which source is missing."""
    model = _video_model(client)
    image = _upload(client, name="frame.png", content_type="image/png", data=PNG)
    status, body = client.call(
        "POST",
        "/api/generations/videos",
        {
            "model_id": model,
            "mode": "extend",
            "prompt": "continue the shot",
            "count": 1,
            "duration_seconds": 4,
            "references": [{"asset_id": image, "role": "reference_video"}],
            "client_request_id": "req_%s" % os.urandom(5).hex(),
        },
    )
    assert status == 400, body
    assert body["error"]["message"] == "This mode needs a source video."


def test_a_derived_request_carries_the_parent_output(studio):
    """The output the parent produced is what an edit or an extension derives from."""
    derived = studio.app.generations._derived_references(
        {"references_json": "[]", "outputs": [{"asset_id": "asset-1"}]},
        "reference_video",
    )
    assert derived == [{"asset_id": "asset-1", "role": "reference_video"}]


def test_a_derived_request_does_not_duplicate_an_output_it_already_carries(studio):
    derived = studio.app.generations._derived_references(
        {
            "references_json": json.dumps([{"asset_id": "asset-1", "role": "reference_video"}]),
            "outputs": [{"asset_id": "asset-1"}],
        },
        "reference_video",
    )
    assert derived == [{"asset_id": "asset-1", "role": "reference_video"}]


def test_a_parent_with_no_ingested_output_contributes_nothing(studio):
    derived = studio.app.generations._derived_references(
        {"references_json": "[]", "outputs": [{"asset_id": None, "ingest_error": "fetch failed"}]},
        "first_frame",
    )
    assert derived == []
