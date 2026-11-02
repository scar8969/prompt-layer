"""The canvas: a graph compiler, and a run that only queues what it can feed.

Three properties are worth pinning, because each of them is a way this feature
could lie:

* **The order is real.** A node is never queued before the nodes that feed it have
  a stored output, so a chained request never carries a source that does not exist.
* **The graph is checked, not trusted.** A cycle, an edge to a missing node, an
  edge from a note and an unknown edge role are all refused by name, at save and
  again at run.
* **A run reports what happened.** A node is ``done`` only when its job completed
  *and* an output was stored; a failed upstream skips its branch instead of letting
  it run with a missing source.

Run:  python -m pytest tests/test_canvas.py -q
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

from studio import canvas  # noqa: E402
from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.http import build_server  # noqa: E402

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# The graph compiler, as a pure function
# ---------------------------------------------------------------------------


def _node(node_id: str, *, kind: str = "generate", **extra):
    base = {
        "id": node_id,
        "kind": kind,
        "label": node_id,
        "position": {"x": extra.pop("x", 0), "y": extra.pop("y", 0)},
    }
    if kind == "generate":
        base.update(
            {
                "prompt": "a lighthouse in fog",
                "model_id": "model-1",
                **extra,
            }
        )
    else:
        base.update(extra)
    return base


def test_a_linear_chain_orders_upstream_first():
    nodes = [_node("c", x=300), _node("a", x=0), _node("b", x=150)]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    clean_nodes, clean_edges, order = canvas.normalise_graph(nodes, edges)
    assert order == ["a", "b", "c"]
    assert len(clean_nodes) == 3 and len(clean_edges) == 2


def test_two_branches_are_ordered_deterministically_by_position():
    nodes = [_node("root", x=0), _node("left", x=100, y=0), _node("right", x=100, y=200)]
    edges = [{"from": "root", "to": "right"}, {"from": "root", "to": "left"}]
    _nodes, _edges, order = canvas.normalise_graph(nodes, edges)
    assert order == ["root", "left", "right"]
    # And again, from a differently ordered document: the order is a property of
    # the graph, not of the order the JSON happened to list it in.
    _nodes, _edges, again = canvas.normalise_graph(list(reversed(nodes)), list(reversed(edges)))
    assert again == order


def test_a_cycle_is_refused_and_names_the_nodes_on_it():
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
    with pytest.raises(canvas.ApiError) as caught:
        canvas.normalise_graph(nodes, edges)
    error = caught.value
    assert error.field == "edges"
    assert "loop" in error.message
    assert set(error.details["nodes"]) == {"a", "b", "c"}


def test_an_edge_to_a_missing_node_is_refused():
    with pytest.raises(canvas.ApiError) as caught:
        canvas.normalise_graph([_node("a")], [{"from": "a", "to": "ghost"}])
    assert "ghost" in caught.value.message


def test_an_unknown_edge_role_is_refused_rather_than_ignored():
    with pytest.raises(canvas.ApiError) as caught:
        canvas.normalise_graph([_node("a"), _node("b")], [{"from": "a", "to": "b", "role": "maybe"}])
    assert caught.value.field == "edges"
    assert "maybe" in caught.value.message


def test_a_note_cannot_take_part_in_an_edge():
    nodes = [_node("a"), _node("note", kind="note", text="why this branch exists")]
    with pytest.raises(canvas.ApiError) as caught:
        canvas.normalise_graph(nodes, [{"from": "note", "to": "a"}])
    assert "produces nothing" in caught.value.message


def test_a_generate_node_without_a_prompt_or_model_is_refused():
    with pytest.raises(canvas.ApiError):
        canvas.normalise_graph([{"id": "a", "model_id": "m", "prompt": "   "}], [])
    with pytest.raises(canvas.ApiError):
        canvas.normalise_graph([{"id": "a", "prompt": "x"}], [])


def test_duplicate_node_ids_are_refused():
    with pytest.raises(canvas.ApiError) as caught:
        canvas.normalise_graph([_node("a"), _node("a")], [])
    assert "share the id" in caught.value.message


def test_the_plan_resolves_each_nodes_upstream():
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [
        {"from": "a", "to": "b", "role": "input"},
        {"from": "a", "to": "c", "role": "reference", "reference_role": "style"},
    ]
    steps = canvas.plan(nodes, edges)
    by_id = {step["node_id"]: step for step in steps}
    assert by_id["a"]["source_from"] is None
    assert by_id["b"]["source_from"] == "a"
    assert by_id["c"]["source_from"] is None
    assert by_id["c"]["references_from"] == [{"from": "a", "role": "style"}]


def test_two_input_edges_into_one_node_is_refused():
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [
        {"from": "a", "to": "c", "role": "input"},
        {"from": "b", "to": "c", "role": "input"},
    ]
    with pytest.raises(canvas.ApiError) as caught:
        canvas.plan(nodes, edges)
    assert "two input edges" in caught.value.message


def test_a_note_is_absent_from_the_plan_but_present_in_the_summary():
    nodes = [_node("a"), _node("note", kind="note", text="hello")]
    summary = canvas.describe(nodes, [])
    assert summary["note_count"] == 1
    assert summary["generate_count"] == 1
    assert [step["node_id"] for step in canvas.plan(nodes, [])] == ["a"]


# ---------------------------------------------------------------------------
# Over real HTTP
# ---------------------------------------------------------------------------


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
            {
                "email": "canvas-%s@example.com" % os.urandom(6).hex(),
                "password": PASSWORD,
                "display_name": "Ada",
            },
        )
        assert status == 201, payload
        if payload.get("session", {}).get("csrf_token"):
            self.csrf = payload["session"]["csrf_token"]

    def model(self, modality: str = "image", *, key: str = "", modes: list | None = None) -> dict:
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
                "model_key": key or ("mock/placeholder-%s" % modality),
                "display_name": "Mock %s" % modality,
                "modality": modality,
                "modes": modes or [mode, "image_to_image" if modality == "image" else "image_to_video"],
                "capabilities": {"max_outputs": 1},
            },
        )
        assert status == 201, payload
        return payload["model"]


@pytest.fixture(scope="module")
def studio(tmp_path_factory, studio_database):
    data_dir = str(tmp_path_factory.mktemp("canvas"))
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


def _poll_run(client, canvas_id: str, run_id: str, *, attempts: int = 40):
    """Poll the run the way the screen does, until it stops moving."""
    body = {}
    for _ in range(attempts):
        status, body = client.call(
            "GET", "/api/canvases/%s/runs/%s" % (canvas_id, run_id)
        )
        assert status == 200, body
        if body["run"]["status"] in ("completed", "failed", "cancelled"):
            return body["run"]
        time.sleep(0.15)
    return body.get("run", {})


def test_a_canvas_needs_a_session(studio):
    anonymous = Client(studio.base)
    status, body = anonymous.call("GET", "/api/canvases")
    assert status == 401, body
    assert body["error"]["code"] == "unauthenticated"


def test_saving_a_canvas_returns_its_summary_and_order(client):
    model = client.model("image")
    status, body = client.call(
        "POST",
        "/api/canvases",
        {
            "name": "Two frames",
            "description": "one after the other",
            "nodes": [
                _node("first", model_id=model["id"], x=0),
                _node("second", model_id=model["id"], x=200),
            ],
            "edges": [{"from": "first", "to": "second", "role": "input"}],
        },
    )
    assert status == 201, body
    saved = body["canvas"]
    assert saved["name"] == "Two frames"
    assert saved["summary"]["order"] == ["first", "second"]
    assert saved["summary"]["generate_count"] == 2


def test_a_cyclic_canvas_is_refused_before_it_is_stored(client):
    model = client.model("image")
    status, body = client.call(
        "POST",
        "/api/canvases",
        {
            "name": "Loop",
            "nodes": [_node("a", model_id=model["id"]), _node("b", model_id=model["id"])],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        },
    )
    assert status == 400, body
    assert body["error"]["field"] == "edges"
    assert "loop" in body["error"]["message"]
    status, listing = client.call("GET", "/api/canvases")
    assert all(item["name"] != "Loop" for item in listing["canvases"])


def test_a_run_chains_a_real_output_into_the_next_node(client, studio):
    """The whole point: node two receives the asset node one really produced."""
    first_model = client.model("image")
    # The mock adapter keeps text-to-image and editing as different placeholder
    # models, the way a real provider publishes different endpoints for them.
    edit_model = client.model("image", key="mock/placeholder-edit", modes=["image_to_image"])
    status, body = client.call(
        "POST",
        "/api/canvases",
        {
            "name": "Chain",
            "nodes": [
                _node("first", model_id=first_model["id"], x=0),
                _node("second", model_id=edit_model["id"], x=200),
            ],
            "edges": [{"from": "first", "to": "second", "role": "input"}],
        },
    )
    assert status == 201, body
    canvas_id = body["canvas"]["id"]

    status, started = client.call("POST", "/api/canvases/%s/runs" % canvas_id)
    assert status == 202, started
    run_id = started["run"]["id"]
    finished = _poll_run(client, canvas_id, run_id)

    assert finished["status"] == "completed", finished
    states = finished["node_states"]
    assert states["first"]["status"] == "done"
    assert states["second"]["status"] == "done"
    assert states["first"]["asset_id"] and states["second"]["asset_id"]
    assert states["first"]["asset_id"] != states["second"]["asset_id"]

    # The second job's request really carried the first job's output as a source.
    second_job = studio.app.repos.jobs.get(
        studio.app.db.query_dict(
            "SELECT workspace_id FROM generation_jobs WHERE id = ?",
            (states["second"]["job_id"],),
        )["workspace_id"],
        states["second"]["job_id"],
    )
    references = second_job["references"]
    assert [item["asset_id"] for item in references] == [states["first"]["asset_id"]]
    assert second_job["mode"] == "image_to_image"


def test_polling_the_same_run_twice_does_not_queue_a_node_twice(client, studio):
    model = client.model("image")
    status, body = client.call(
        "POST",
        "/api/canvases",
        {
            "name": "Once",
            "nodes": [_node("only", model_id=model["id"])],
            "edges": [],
        },
    )
    canvas_id = body["canvas"]["id"]
    status, started = client.call("POST", "/api/canvases/%s/runs" % canvas_id)
    run_id = started["run"]["id"]
    first = _poll_run(client, canvas_id, run_id)
    assert first["status"] == "completed"
    for _ in range(3):
        status, again = client.call("GET", "/api/canvases/%s/runs/%s" % (canvas_id, run_id))
        assert status == 200
        assert again["run"]["node_states"]["only"]["job_id"] == first["node_states"]["only"]["job_id"]

    workspace = studio.app.db.query_dict(
        "SELECT workspace_id FROM canvas_runs WHERE id = ?", (run_id,)
    )["workspace_id"]
    count = studio.app.db.scalar(
        "SELECT COUNT(*) FROM generation_jobs WHERE workspace_id = ?", (workspace,)
    )
    assert int(count) == 1, "one node, one generation"


def test_a_canvas_with_no_generate_node_cannot_start(client):
    status, body = client.call(
        "POST",
        "/api/canvases",
        {"name": "Notes only", "nodes": [_node("n", kind="note", text="just a note")], "edges": []},
    )
    assert status == 201, body
    status, started = client.call("POST", "/api/canvases/%s/runs" % body["canvas"]["id"])
    assert status == 400, started
    assert "no generate node" in started["error"]["message"]


def test_a_canvas_is_workspace_scoped(client):
    model = client.model("image")
    status, body = client.call(
        "POST",
        "/api/canvases",
        {"name": "Private", "nodes": [_node("a", model_id=model["id"])], "edges": []},
    )
    canvas_id = body["canvas"]["id"]

    other = Client(client.base)
    other.register()
    status, error = other.call("GET", "/api/canvases/%s" % canvas_id)
    assert status == 404, error
    status, listing = other.call("GET", "/api/canvases")
    assert status == 200
    assert all(item["id"] != canvas_id for item in listing["canvases"])


def test_editing_a_canvas_revalidates_the_graph(client):
    model = client.model("image")
    status, body = client.call(
        "POST",
        "/api/canvases",
        {"name": "Editable", "nodes": [_node("a", model_id=model["id"])], "edges": []},
    )
    canvas_id = body["canvas"]["id"]
    status, updated = client.call(
        "PATCH",
        "/api/canvases/%s" % canvas_id,
        {
            "nodes": [_node("a", model_id=model["id"]), _node("b", model_id=model["id"])],
            "edges": [{"from": "a", "to": "b"}],
        },
    )
    assert status == 200, updated
    assert updated["canvas"]["summary"]["order"] == ["a", "b"]

    status, refused = client.call(
        "PATCH",
        "/api/canvases/%s" % canvas_id,
        {"edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]},
    )
    assert status == 400, refused
    assert "loop" in refused["error"]["message"]
