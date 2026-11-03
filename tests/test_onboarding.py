"""One key + one URL: discovery, registration, and conversation.

The provider here is a real HTTP server on a loopback socket. That matters: the
whole point of onboarding is that it *calls* the endpoint, so a stubbed transport
would test the classification table and nothing else. The fake provider answers
`/v1/models` and `/v1/chat/completions`, refuses a bad key, and fails one model on
purpose so "available" can be shown to mean "the provider answered".

Run:  python -m pytest tests/test_onboarding.py -q
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import discovery  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402

PASSWORD = "correct horse battery staple"
API_KEY = "sk-test-1234"

MODELS = [
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"},
    {"id": "claude-sonnet-5.5", "name": "Claude Sonnet 5.5"},
    {"id": "broken-chat", "name": "Broken chat"},
    {"id": "gpt-image-2", "name": "GPT Image 2"},
    {"id": "kling-v3", "name": "Kling V3"},
    {"id": "whisper-1", "name": "Whisper"},
    {"id": "text-embedding-3-small", "name": "Embedding"},
]


class FakeProvider(BaseHTTPRequestHandler):
    """A small OpenAI-compatible aggregator."""

    calls: list = []

    def log_message(self, *args):  # noqa: A003 - silence the test server
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        return self.headers.get("Authorization") == "Bearer %s" % API_KEY

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        type(self).calls.append(("GET", self.path))
        if self.path not in ("/v1/models", "/models"):
            return self._json(404, {"error": {"message": "no such path"}})
        if not self._authorised():
            return self._json(401, {"error": {"message": "bad key"}})
        return self._json(200, {"data": MODELS})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls.append(("POST", self.path, body))
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "no such path"}})
        if not self._authorised():
            return self._json(401, {"error": {"message": "bad key"}})
        if body.get("model") == "broken-chat":
            return self._json(500, {"error": {"message": "upstream is sad"}})
        return self._json(
            200,
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )


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
            response = urllib.request.urlopen(request, timeout=60)
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

    def register(self, email: str = "") -> None:
        email = email or "owner-%s@example.com" % os.urandom(6).hex()
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {"email": email, "password": PASSWORD, "display_name": "Ada"},
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]


@pytest.fixture(scope="module")
def provider():
    FakeProvider.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database, provider):
    data_dir = str(tmp_path_factory.mktemp("onboarding"))
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
            "STUDIO_ALLOW_INSECURE_ENDPOINTS": "1",
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound = "http://127.0.0.1:%d" % server.server_address[1]
    yield type("Studio", (), {"app": app, "base": bound, "data_dir": data_dir})
    server.shutdown()
    server.server_close()
    app.shutdown()


@pytest.fixture()
def client(studio):
    client = Client(studio.base)
    client.register()
    return client


def _discover(client, provider, **extra):
    body = {
        "name": "PixStudio",
        "base_url": provider,
        "api_key": API_KEY,
        "allowed_hosts": ["127.0.0.1"],
    }
    body.update(extra)
    return client.call("POST", "/api/onboarding/discover", body)


def _register(client, provider, models, **extra):
    body = {
        "name": "PixStudio",
        "base_url": provider,
        "api_key": API_KEY,
        "allowed_hosts": ["127.0.0.1"],
        "models": models,
    }
    body.update(extra)
    return client.call("POST", "/api/onboarding/register", body)


# ---------------------------------------------------------------------------
# Classification is pure: no network, no database, no provider.
# ---------------------------------------------------------------------------


def test_the_name_table_puts_each_model_in_the_right_family():
    assert discovery.classify("deepseek-v4.1-flash") == discovery.CHAT
    assert discovery.classify("gpt-image-2") == discovery.IMAGE
    assert discovery.classify("gpt-5.5") == discovery.CHAT
    assert discovery.classify("kling-v3") == discovery.VIDEO
    assert discovery.classify("seedance-2.5") == discovery.VIDEO
    assert discovery.classify("muse-video") == discovery.VIDEO
    assert discovery.classify("whisper-1") == discovery.AUDIO
    assert discovery.classify("text-embedding-3-small") == discovery.EMBEDDING
    assert discovery.classify("bge-reranker-v2") == discovery.RERANK
    assert discovery.classify("something-unheard-of") == discovery.UNKNOWN


def test_a_declared_capability_wins_over_the_name():
    """An aggregator that says what a model is must be believed over a guess."""
    assert discovery.classify("gpt-5.5", {"modality": "image"}) == discovery.IMAGE
    assert discovery.classify("mystery", {"architecture": {"modality": "text->video"}}) == discovery.VIDEO
    assert discovery.classify("mystery", {"architecture": {"modality": "text->text"}}) == discovery.CHAT


def test_the_proposal_marks_only_drivable_families_as_drivable():
    plan = discovery.proposal(MODELS)
    by_key = {item["model_key"]: item for item in plan["models"]}
    assert by_key["deepseek-v4.1-flash"]["drivable"] is True
    assert by_key["gpt-image-2"]["drivable"] is True
    assert by_key["kling-v3"]["drivable"] is True
    assert by_key["whisper-1"]["drivable"] is False
    assert by_key["text-embedding-3-small"]["drivable"] is False
    assert by_key["kling-v3"]["modality"] == "video"
    assert by_key["kling-v3"]["modes"] == ["text_to_video"]
    assert by_key["kling-v3"]["reason"]
    assert plan["default_chat"] == "deepseek-v4.1-flash"
    assert plan["counts"][discovery.CHAT] == 3
    assert plan["counts"][discovery.IMAGE] == 1


def test_the_proposal_falls_back_when_the_wanted_chat_model_is_absent():
    plan = discovery.proposal([{"id": "claude-sonnet-5.5"}])
    assert plan["default_chat"] == "claude-sonnet-5.5"
    assert plan["warnings"]


def test_api_root_does_not_double_v1():
    assert discovery.api_root("https://api.example.com") == "https://api.example.com/v1"
    assert discovery.api_root("https://api.example.com/v1/") == "https://api.example.com/v1"


# ---------------------------------------------------------------------------
# Discovery over HTTP: the provider is asked, and its answer is the proposal.
# ---------------------------------------------------------------------------


def test_discovery_reads_the_list_and_probes_the_chat_models(client, provider):
    status, body = _discover(client, provider)
    assert status == 200, body
    plan = body["plan"]
    assert plan["provider"]["base_url"].rstrip("/") == provider
    assert plan["default_chat"] == "deepseek-v4.1-flash"
    assert plan["probe_summary"] == {"probed": 3, "available": 2, "failed": 1, "max_probes": 24}
    by_key = {item["model_key"]: item for item in plan["models"]}
    assert by_key["deepseek-v4.1-flash"]["available"] is True
    assert by_key["deepseek-v4.1-flash"]["probe"]["latency_ms"] >= 0
    assert by_key["broken-chat"]["available"] is False
    assert "upstream is sad" in by_key["broken-chat"]["probe"]["error"]
    # Image and video models are reported, never silently probed.
    assert "probe" not in by_key["gpt-image-2"]
    assert "probe" not in by_key["kling-v3"]


def test_discovery_refuses_a_wrong_key_with_the_providers_status(client, provider):
    status, body = _discover(client, provider, api_key="sk-wrong")
    assert status == 400, body
    assert body["error"]["field"] == "api_key"
    assert body["error"]["details"]["status"] == 401


def test_discovery_can_skip_probing(client, provider):
    status, body = _discover(client, provider, probe_chat=False)
    assert status == 200, body
    assert "probe_summary" not in body["plan"]
    assert all("probe" not in item for item in body["plan"]["models"])


def test_discovery_writes_nothing(client, provider):
    status, before = client.call("GET", "/api/connections")
    assert status == 200
    _discover(client, provider)
    status, after = client.call("GET", "/api/connections")
    assert status == 200
    assert len(after["connections"]) == len(before["connections"])


# ---------------------------------------------------------------------------
# Registration: one connection, the chosen models, one default conversation.
# ---------------------------------------------------------------------------


def test_registering_creates_one_connection_and_the_chosen_models(client, provider):
    status, body = _register(
        client,
        provider,
        [
            {"model_key": "deepseek-v4.1-flash", "family": "chat", "display_name": "DeepSeek V4.1 Flash"},
            {"model_key": "gpt-image-2", "family": "image", "display_name": "GPT Image 2"},
        ],
    )
    assert status == 201, body
    connection = body["connection"]
    assert connection["adapter_type"] == "openai_images"
    assert connection["base_url"].rstrip("/") == provider
    assert connection["has_credential"] is True
    keys = {model["model_key"]: model for model in body["models"]}
    assert keys["deepseek-v4.1-flash"]["modality"] == "other"
    assert keys["deepseek-v4.1-flash"]["modes"] == []
    assert keys["gpt-image-2"]["modality"] == "image"
    assert set(keys["gpt-image-2"]["modes"]) == {"text_to_image", "image_to_image"}
    assert body["default_chat"] == "deepseek-v4.1-flash"
    assert body["default_chat_model_id"] == keys["deepseek-v4.1-flash"]["id"]


def test_registering_refuses_a_family_this_build_cannot_drive(client, provider):
    status, body = _register(
        client,
        provider,
        [{"model_key": "whisper-1", "family": "audio"}],
    )
    assert status == 400, body
    assert body["error"]["field"] == "models"
    assert "cannot drive" in body["error"]["message"]


def test_registering_video_gets_its_own_protocol_connection(client, provider):
    """One key, two protocols: images and video are different wire formats."""
    status, body = _register(
        client,
        provider,
        [
            {"model_key": "deepseek-v4.1-flash", "family": "chat"},
            {"model_key": "gpt-image-2", "family": "image"},
            {"model_key": "kling-v3", "family": "video", "display_name": "Kling V3"},
        ],
    )
    assert status == 201, body
    adapters = [item["adapter_type"] for item in body["connections"]]
    assert adapters == ["openai_images", "declarative_http"]
    assert body["connection"]["adapter_type"] == "openai_images"
    keys = {model["model_key"]: model for model in body["models"]}
    video = keys["kling-v3"]
    assert video["modality"] == "video"
    assert video["modes"] == ["text_to_video"]
    # Chat rode on the images connection rather than opening a third one.
    assert keys["deepseek-v4.1-flash"]["connection_id"] == body["connections"][0]["id"]
    assert body["default_chat_model_id"] == keys["deepseek-v4.1-flash"]["id"]
    assert body["notes"]["adapter"] == "openai_images, declarative_http"


def test_registering_video_alone_uses_the_videos_mapping(client, provider):
    status, body = _register(
        client,
        provider,
        [{"model_key": "kling-v3", "family": "video"}],
    )
    assert status == 201, body
    assert len(body["connections"]) == 1
    assert body["connection"]["adapter_type"] == "declarative_http"


def test_registration_requires_selected_models(client, provider):
    status, body = _register(client, provider, [])
    assert status == 400
    assert body["error"]["field"] == "models"


# ---------------------------------------------------------------------------
# Conversation: the registered chat model answers, and image models cannot.
# ---------------------------------------------------------------------------


@pytest.fixture()
def onboarded(client, provider):
    status, body = _register(
        client,
        provider,
        [
            {"model_key": "deepseek-v4.1-flash", "family": "chat"},
            {"model_key": "gpt-image-2", "family": "image"},
        ],
    )
    assert status == 201, body
    models = {model["model_key"]: model for model in body["models"]}
    return client, models


def test_chat_models_lists_only_conversation_models(onboarded):
    client, models = onboarded
    status, body = client.call("GET", "/api/chat/models")
    assert status == 200
    listed = [item["model_key"] for item in body["models"]]
    assert listed == ["deepseek-v4.1-flash"]
    assert body["default_model_id"] == models["deepseek-v4.1-flash"]["id"]


def test_chat_returns_the_providers_answer_and_usage(onboarded):
    client, models = onboarded
    status, body = client.call(
        "POST",
        "/api/chat",
        {
            "model_id": models["deepseek-v4.1-flash"]["id"],
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 8,
        },
    )
    assert status == 200, body
    assert body["message"] == {"role": "assistant", "content": "ok"}
    assert body["model_key"] == "deepseek-v4.1-flash"
    assert body["usage"]["completion_tokens"] == 1
    assert body["provider"]["connection_id"] == models["deepseek-v4.1-flash"]["connection_id"]
    assert body["latency_ms"] >= 0


def test_chat_refuses_an_image_model_by_name(onboarded):
    client, models = onboarded
    status, body = client.call(
        "POST",
        "/api/chat",
        {
            "model_id": models["gpt-image-2"]["id"],
            "messages": [{"role": "user", "content": "draw a cat"}],
        },
    )
    assert status == 400
    assert body["error"]["field"] == "model_id"
    assert "not a conversation model" in body["error"]["message"]


def test_chat_passes_a_provider_failure_through_instead_of_inventing_an_answer(client, provider):
    status, body = _register(client, provider, [{"model_key": "broken-chat", "family": "chat"}])
    assert status == 201, body
    model = body["models"][0]
    status, body = client.call(
        "POST",
        "/api/chat",
        {"model_id": model["id"], "messages": [{"role": "user", "content": "hello"}]},
    )
    assert status == 502, body
    assert body["error"]["code"] == "provider_error"
    assert "upstream is sad" in body["error"]["provider_message"]


def test_chat_validates_the_conversation_before_sending(onboarded):
    client, models = onboarded
    status, body = client.call("POST", "/api/chat", {"model_id": models["deepseek-v4.1-flash"]["id"], "messages": []})
    assert status == 400
    assert body["error"]["field"] == "messages"
    status, body = client.call(
        "POST",
        "/api/chat",
        {
            "model_id": models["deepseek-v4.1-flash"]["id"],
            "messages": [{"role": "wizard", "content": "hello"}],
        },
    )
    assert status == 400
    assert body["error"]["field"] == "messages"
