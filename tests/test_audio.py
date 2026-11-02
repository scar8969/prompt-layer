"""Sound on a clip: voiceover, dub, lip sync, generated track.

The honest claims here are about where the bytes come from:

* a sound operation is a **derived job**; it never replaces the clip it came from;
* lip sync is refused without a track and a voiceover/dub is refused without a
  script, because a silent lip sync is not a lip sync;
* the stored asset's kind is decided by the returned bytes -- a voiceover is an
  `audio` asset even though the job ran on a video model;
* a model that does not declare the mode is refused by the model profile, the
  same way every other unsupported mode is.

Run:  python -m pytest tests/test_audio.py -q
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
            return status, {"_body": payload[:300].decode("utf-8", "replace")}

    def register(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {"email": "audio-%s@example.com" % os.urandom(6).hex(), "password": PASSWORD, "display_name": "Ada"},
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]

    def model(self, model_key: str, modes: list[str]) -> dict:
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
        status, payload = self.call(
            "POST",
            "/api/connections/%s/models" % connection["id"],
            {
                "model_key": model_key,
                "display_name": model_key.rsplit("/", 1)[-1],
                "modality": "video",
                "modes": modes,
                "capabilities": {"max_outputs": 1, "supports_audio": "audio" in modes},
            },
        )
        assert status == 201, payload
        return payload["model"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("audio"))
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


def _drive(studio, workspace_id: str, job_id: str) -> dict:
    for _ in range(30):
        current = studio.app.repos.jobs.get(workspace_id, job_id)
        if current["status"] in ("completed", "failed", "cancelled"):
            return current
        studio.app.runner.drive(current)
        time.sleep(0.1)
    return studio.app.repos.jobs.get(workspace_id, job_id)


def _video_job(client) -> tuple[dict, dict]:
    """A finished clip to derive sound from."""
    model = client.model("mock/placeholder-video", ["text_to_video"])
    status, body = client.call(
        "POST",
        "/api/generations/videos",
        {"model_id": model["id"], "mode": "text_to_video", "prompt": "a lighthouse in fog"},
    )
    assert status == 202, body
    return model, body["job"]


def test_a_voiceover_is_a_new_job_and_an_audio_asset(client, studio):
    video_model, parent = _video_job(client)
    finished = _drive(studio, parent["workspace_id"], parent["id"])
    assert finished["status"] == "completed", finished.get("error_message")

    audio_model = client.model("mock/placeholder-audio", ["voiceover", "dub", "lip_sync", "audio"])
    status, body = client.call(
        "POST",
        "/api/generations/%s/audio" % parent["id"],
        {"mode": "voiceover", "script": "The light turns once every twelve seconds.", "model_id": audio_model["id"]},
    )
    assert status == 202, body
    job = body["job"]
    assert job["mode"] == "voiceover"
    assert job["relation"] == "audio"
    assert job["parent_job_id"] == parent["id"]

    result = _drive(studio, job["workspace_id"], job["id"])
    assert result["status"] == "completed", result.get("error_message")
    asset_id = result["outputs"][0]["asset_id"]
    asset = studio.app.repos.assets.get(job["workspace_id"], asset_id)
    assert asset["kind"] == "audio", asset
    assert asset["mime_type"].startswith("audio/")

    # The parent clip is untouched: a derived request never replaces its source.
    still_there = studio.app.repos.jobs.get(parent["workspace_id"], parent["id"])
    assert still_there["status"] == "completed"


def test_lip_sync_without_a_track_is_refused_by_name(client, studio):
    _video_model, parent = _video_job(client)
    _drive(studio, parent["workspace_id"], parent["id"])
    audio_model = client.model("mock/placeholder-audio", ["voiceover", "dub", "lip_sync", "audio"])
    status, body = client.call(
        "POST",
        "/api/generations/%s/audio" % parent["id"],
        {"mode": "lip_sync", "model_id": audio_model["id"]},
    )
    assert status == 400, body
    assert body["error"]["field"] == "track_asset_id"
    assert "track" in body["error"]["message"].lower()


def test_a_dub_needs_a_script(client, studio):
    _video_model, parent = _video_job(client)
    _drive(studio, parent["workspace_id"], parent["id"])
    audio_model = client.model("mock/placeholder-audio", ["voiceover", "dub", "lip_sync", "audio"])
    status, body = client.call(
        "POST",
        "/api/generations/%s/audio" % parent["id"],
        {"mode": "dub", "model_id": audio_model["id"]},
    )
    assert status == 400, body
    assert body["error"]["field"] == "script"


def test_an_audio_mode_the_model_does_not_declare_is_refused(client, studio):
    _video_model, parent = _video_job(client)
    _drive(studio, parent["workspace_id"], parent["id"])
    # The plain video model does not declare voiceover, so it cannot be used for one.
    plain = client.model("mock/placeholder-video", ["text_to_video"])
    status, body = client.call(
        "POST",
        "/api/generations/%s/audio" % parent["id"],
        {"mode": "voiceover", "script": "hello", "model_id": plain["id"]},
    )
    assert status == 400, body
    assert body["error"]["code"] in ("capability_unsupported", "validation_failed")


def test_an_unknown_audio_operation_is_refused(client, studio):
    _video_model, parent = _video_job(client)
    _drive(studio, parent["workspace_id"], parent["id"])
    status, body = client.call(
        "POST",
        "/api/generations/%s/audio" % parent["id"],
        {"mode": "beatbox"},
    )
    assert status == 400, body
    assert body["error"]["field"] == "mode"
