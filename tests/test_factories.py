"""Factories: a brief planned into a multi-shot canvas.

What is pinned here:

* planning is deterministic and calls no provider — the same brief always
  produces the same graph, and a plan with no product is refused rather than
  filled with a placeholder;
* a photodump's styles come from the catalogue, and a style that is not in it is
  refused by name;
* saving a plan stores a real canvas whose nodes the canvas validator accepts, so
  a factory cannot produce a document the run engine would reject;
* a plan is free; only running the canvas queues jobs.

Run:  python -m pytest tests/test_factories.py -q
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

from studio import factories  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.errors import ApiError  # noqa: E402
from studio.http import build_server  # noqa: E402

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# The planner, as a pure function
# ---------------------------------------------------------------------------


def test_the_ugc_factory_plans_four_beats_in_reading_order():
    plan = factories.plan("ugc", model_id="m1", product="Harbor Lamp", benefit="light that fades")
    assert [node["id"] for node in plan["nodes"]] == ["ugc-hook", "ugc-proof", "ugc-demo", "ugc-cta"]
    assert plan["summary"]["generate_count"] == 4
    for node in plan["nodes"]:
        assert node["model_id"] == "m1"
        assert "Harbor Lamp" in node["prompt"]
        assert "light that fades" in node["prompt"]
        assert "no rendered text" in node["prompt"]


def test_the_same_brief_plans_the_same_graph():
    first = factories.plan("fashion", model_id="m1", product="Linen Coat", lens="85mm", lighting="window light")
    second = factories.plan("fashion", model_id="m1", product="Linen Coat", lens="85mm", lighting="window light")
    assert first == second
    assert [node["id"] for node in first["nodes"]] == [
        "fashion-full_length",
        "fashion-detail",
        "fashion-movement",
        "fashion-editorial",
    ]
    assert "85mm" in first["nodes"][0]["prompt"]


def test_a_plan_without_a_product_is_refused():
    with pytest.raises(ApiError) as caught:
        factories.plan("ugc", model_id="m1", product="   ")
    assert caught.value.field == "product"


def test_a_plan_without_a_model_is_refused():
    with pytest.raises(ApiError) as caught:
        factories.plan("ugc", model_id="", product="Harbor Lamp")
    assert caught.value.field == "model_id"


def test_an_unknown_factory_is_refused_by_name():
    with pytest.raises(ApiError) as caught:
        factories.plan("billboard", model_id="m1", product="Harbor Lamp")
    assert caught.value.field == "kind"
    assert "billboard" in caught.value.message


def test_a_photodump_uses_catalogue_styles_and_names_none_it_does_not_have():
    plan = factories.plan("photodump", model_id="m1", product="Harbor Lamp")
    assert plan["nodes"]
    assert all(node["id"].startswith("photodump-") for node in plan["nodes"])
    with pytest.raises(ApiError) as caught:
        factories.plan("photodump", model_id="m1", product="Harbor Lamp", styles=["not.a.style"])
    assert caught.value.field == "styles"
    assert "not.a.style" in caught.value.message


def test_a_photodump_is_capped():
    from studio.presets_data import presets_for

    many = [entry["key"] for entry in presets_for("image")][: factories.MAX_NODES + 1]
    with pytest.raises(ApiError) as caught:
        factories.plan("photodump", model_id="m1", product="x", styles=many)
    assert caught.value.field == "styles"


def test_every_plan_satisfies_the_canvas_validator():
    from studio.canvas import normalise_graph

    for kind in factories.KINDS:
        plan = factories.plan(kind, model_id="m1", product="Harbor Lamp")
        nodes, edges, order = normalise_graph(plan["nodes"], plan["edges"])
        assert len(nodes) == len(plan["nodes"])
        assert order == plan["summary"]["order"]


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
            {"email": "factory-%s@example.com" % os.urandom(6).hex(), "password": PASSWORD, "display_name": "Ada"},
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
    data_dir = str(tmp_path_factory.mktemp("factories"))
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


def test_the_factory_list_names_what_each_one_produces(client):
    status, body = client.call("GET", "/api/factories")
    assert status == 200, body
    assert [item["kind"] for item in body["factories"]] == list(factories.KINDS)
    for item in body["factories"]:
        assert item["label"] and item["summary"]
        assert item["node_count"] > 0


def test_a_plan_is_computed_and_costs_nothing(client, studio):
    model = client.image_model()
    status, body = client.call(
        "POST",
        "/api/factories/plan",
        {"kind": "ugc", "model_id": model["id"], "product": "Harbor Lamp", "benefit": "light that fades"},
    )
    assert status == 200, body
    plan = body["plan"]
    assert len(plan["nodes"]) == 4
    # No job was queued by planning.
    status, jobs = client.call("GET", "/api/jobs?limit=5")
    assert status == 200
    assert jobs["jobs"] == []


def test_saving_a_plan_stores_a_canvas_the_run_engine_accepts(client):
    model = client.image_model()
    status, body = client.call(
        "POST",
        "/api/factories/canvases",
        {
            "kind": "fashion",
            "model_id": model["id"],
            "product": "Linen Coat",
            "benefit": "made for wind",
            "name": "Lookbook",
        },
    )
    assert status == 201, body
    canvas = body["canvas"]
    assert canvas["name"] == "Lookbook"
    assert canvas["revision"] == 1
    assert canvas["summary"]["generate_count"] == 4
    assert [node["id"] for node in canvas["nodes"]] == body["plan"]["summary"]["order"]

    # The canvas is listed and readable, which is what the screen does next.
    status, listing = client.call("GET", "/api/canvases")
    assert status == 200
    assert any(item["id"] == canvas["id"] for item in listing["canvases"])


def test_an_unknown_field_in_a_plan_is_refused(client):
    model = client.image_model()
    status, body = client.call(
        "POST",
        "/api/factories/plan",
        {"kind": "ugc", "model_id": model["id"], "product": "Harbor Lamp", "script": "buy now"},
    )
    assert status == 400, body
    assert "script" in body["error"]["message"]


def test_a_plan_cannot_name_a_model_outside_the_workspace(client):
    status, body = client.call(
        "POST",
        "/api/factories/plan",
        {"kind": "ugc", "model_id": "00000000-0000-0000-0000-000000000000", "product": "Harbor Lamp"},
    )
    assert status == 404, body
    assert body["error"]["code"] == "not_found"
