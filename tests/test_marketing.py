"""Marketing Studio: a page read deterministically, renders queued explicitly.

The claims pinned here:

* the page reader takes only what the page states, and says which tag each fact
  came from; a page that states nothing is refused rather than summarised into a
  plausible claim;
* a brief with no benefit carries a warning instead of inventing one;
* the campaign route queues one ordinary generation per format on the chosen
  model and returns their ids -- it does not call a provider itself;
* a format outside the vocabulary is refused by name.

Run:  python -m pytest tests/test_marketing.py -q
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

from studio import marketing  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.errors import ApiError  # noqa: E402
from studio.http import build_server  # noqa: E402

PASSWORD = "correct horse battery staple"

PAGE = """<!doctype html><html><head>
<title>Fallback title</title>
<meta property="og:title" content="Harbor Lamp" />
<meta property="og:description" content="A brass lamp that dims like sunset." />
<meta property="og:site_name" content="Harbor" />
<meta property="og:image" content="https://example.com/lamp.jpg" />
</head><body>
<h1>Harbor Lamp</h1>
<ul><li>Solid brass body</li><li>Dims over ninety minutes</li></ul>
</body></html>"""


# ---------------------------------------------------------------------------
# The reader and the brief, as pure functions
# ---------------------------------------------------------------------------


def test_the_reader_prefers_open_graph_and_says_where_each_fact_came_from():
    facts = marketing.parse_product_page(PAGE, "https://example.com/lamp")
    assert facts["title"] == "Harbor Lamp"
    assert facts["description"] == "A brass lamp that dims like sunset."
    assert facts["site_name"] == "Harbor"
    assert facts["sources"]["title"] == "og:title"
    assert facts["sources"]["description"] == "og:description"
    assert facts["features"] == ["Solid brass body", "Dims over ninety minutes"]
    assert facts["missing"] == []


def test_the_reader_falls_back_to_title_and_h1_in_order():
    facts = marketing.parse_product_page("<title>Only a title</title>", "https://example.com/x")
    assert facts["title"] == "Only a title"
    assert facts["sources"]["title"] == "title"
    assert facts["missing"] == ["description"]

    facts = marketing.parse_product_page("<h1>Heading only</h1>", "https://example.com/x")
    assert facts["title"] == "Heading only"
    assert facts["sources"]["title"] == "h1"


def test_a_page_that_states_nothing_invents_nothing():
    facts = marketing.parse_product_page("<html><body></body></html>", "https://example.com/x")
    assert facts["title"] is None
    assert facts["description"] is None
    assert facts["missing"] == ["title", "description"]
    assert facts["features"] == []


def test_a_brief_with_no_claim_warns_instead_of_inventing_one():
    facts = marketing.parse_product_page("<title>Harbor Lamp</title>", "https://example.com/x")
    brief = marketing.build_brief(facts, audience="", benefit="")
    assert brief["product"] == "Harbor Lamp"
    assert any("no claim" in warning.lower() for warning in brief["warnings"])
    assert any("audience" in warning.lower() for warning in brief["warnings"])


def test_tone_changes_the_hook_but_not_the_stated_fact():
    facts = marketing.parse_product_page(PAGE, "https://example.com/lamp")
    direct = marketing.build_brief(facts, audience="readers", benefit="light that fades")
    playful = marketing.build_brief(facts, audience="readers", benefit="light that fades", tone="playful")
    assert direct["hook"] != playful["hook"]
    # The claim is the author's words in both, and the product is the page's.
    assert "light that fades" in direct["formats"][0]["prompt"]
    assert "Harbor Lamp" in direct["formats"][0]["prompt"]


def test_an_unknown_tone_is_refused():
    facts = marketing.parse_product_page(PAGE, "https://example.com/lamp")
    with pytest.raises(ApiError) as caught:
        marketing.build_brief(facts, tone="shouty")
    assert caught.value.field == "tone"


def test_every_format_has_its_own_frame():
    facts = marketing.parse_product_page(PAGE, "https://example.com/lamp")
    brief = marketing.build_brief(facts, benefit="light that fades")
    assert [item["key"] for item in brief["formats"]] == list(marketing.FORMATS)
    assert len({item["aspect_ratio"] for item in brief["formats"]}) == len(marketing.FORMATS)
    for item in brief["formats"]:
        assert item["prompt"].endswith(".")
        assert "no text" in item["prompt"]


def test_an_unknown_format_is_refused_by_name():
    with pytest.raises(ApiError) as caught:
        marketing.validate_formats(["square_1x1", "billboard"])
    assert caught.value.field == "formats"
    assert "billboard" in caught.value.message


# ---------------------------------------------------------------------------
# Over real HTTP
# ---------------------------------------------------------------------------


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
            status, payload = response.status, response.read()
            cookies = response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
            cookies = error.headers.get_all("Set-Cookie") or []
        for line in cookies:
            name, _, value = line.split(";")[0].partition("=")
            self.cookies[name.strip()] = value.strip()
        if self.cookies.get("studio_csrf"):
            self.csrf = self.cookies["studio_csrf"]
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, {"_body": payload[:300].decode("utf-8", "replace")}

    def register(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/auth/register",
            {"email": "mkt-%s@example.com" % os.urandom(6).hex(), "password": PASSWORD, "display_name": "Ada"},
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]

    def image_model(self) -> dict:
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
                "model_key": "mock/placeholder-image",
                "display_name": "Mock image",
                "modality": "image",
                "modes": ["text_to_image"],
                "capabilities": {"max_outputs": 1},
            },
        )
        assert status == 201, payload
        return payload["model"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("marketing"))
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


def test_the_formats_route_lists_the_vocabulary(client):
    status, body = client.call("GET", "/api/marketing/formats")
    assert status == 200, body
    assert [item["key"] for item in body["formats"]] == list(marketing.FORMATS)
    assert body["tones"] == list(marketing.TONES)


def test_a_pasted_brief_is_composed_without_calling_a_model(client):
    status, body = client.call(
        "POST",
        "/api/marketing/brief",
        {
            "title": "Harbor Lamp",
            "description": "A brass lamp that dims like sunset.",
            "benefit": "light that fades",
            "audience": "readers",
            "tone": "warm",
        },
    )
    assert status == 200, body
    brief = body["brief"]
    assert brief["product"] == "Harbor Lamp"
    assert brief["sources"]["title"] == "pasted"
    assert "light that fades" in brief["formats"][0]["prompt"]
    assert brief["warnings"] == []


def test_a_brief_with_nothing_to_read_is_refused(client):
    status, body = client.call("POST", "/api/marketing/brief", {})
    assert status == 400, body
    assert body["error"]["field"] == "title"


def test_a_campaign_queues_one_job_per_format(client, studio):
    model = client.image_model()
    status, body = client.call(
        "POST",
        "/api/marketing/campaigns",
        {
            "model_id": model["id"],
            "title": "Harbor Lamp",
            "description": "A brass lamp that dims like sunset.",
            "benefit": "light that fades",
            "formats": ["square_1x1", "story_9x16"],
        },
    )
    assert status == 202, body
    campaign = body["campaign"]
    assert [item["format"] for item in campaign["renders"]] == ["square_1x1", "story_9x16"]
    assert all(item["job_id"] for item in campaign["renders"])

    # The jobs really exist in this workspace's history.
    status, listing = client.call("GET", "/api/jobs?limit=10")
    assert status == 200, listing
    returned = {item["id"] for item in listing["jobs"]}
    for render in campaign["renders"]:
        assert render["job_id"] in returned


def test_a_campaign_needs_a_model(client):
    status, body = client.call(
        "POST",
        "/api/marketing/campaigns",
        {"title": "Harbor Lamp", "description": "x"},
    )
    assert status == 400, body
    assert body["error"]["field"] == "model_id"


def test_a_campaign_refuses_an_unknown_format(client):
    model = client.image_model()
    status, body = client.call(
        "POST",
        "/api/marketing/campaigns",
        {"model_id": model["id"], "title": "Harbor Lamp", "description": "x", "formats": ["billboard"]},
    )
    assert status == 400, body
    assert body["error"]["field"] == "formats"


def test_a_campaign_needs_an_image_model(client):
    status, payload = client.call(
        "POST",
        "/api/connections",
        {"name": "mock-v", "adapter_type": "mock", "base_url": "http://127.0.0.1:9/mock", "auth_scheme": "none"},
    )
    assert status == 201, payload
    connection = payload["connection"]
    status, payload = client.call(
        "POST",
        "/api/connections/%s/models" % connection["id"],
        {
            "model_key": "mock/placeholder-video",
            "display_name": "Mock video",
            "modality": "video",
            "modes": ["text_to_video"],
            "capabilities": {"max_outputs": 1},
        },
    )
    assert status == 201, payload
    status, body = client.call(
        "POST",
        "/api/marketing/campaigns",
        {"model_id": payload["model"]["id"], "title": "Harbor Lamp", "description": "x"},
    )
    assert status == 400, body
    assert body["error"]["field"] == "model_id"
