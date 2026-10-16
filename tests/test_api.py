"""HTTP tests for the Vercel functions in /api.

Each function file exports a `handler` (a BaseHTTPRequestHandler subclass), which
is what Vercel's Python runtime mounts. These tests mount the same classes on a
real socket and speak real HTTP to them, so the thing under test is the deployed
entry point, not a re-implementation of it.

Run:  python -m pytest -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
API_DIR = os.path.join(REPO, "api")


def _load_function(name: str):
    """Import api/<name>.py the way Vercel does: by file, expecting `handler`."""
    spec = importlib.util.spec_from_file_location(f"vf_{name}", os.path.join(API_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler


@pytest.fixture(scope="module", params=["health", "models", "preflight", "improve", "compile", "presets",
                                        "storyboard", "reference", "video"])
def function_server(request):
    """One server per function, mounted at the path Vercel would use."""
    handler = _load_function(request.param)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base: str, path: str = "/"):
    with urllib.request.urlopen(base + path, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_every_function_in_api_exports_a_handler_and_nothing_is_a_dependency():
    """The deployment shape, asserted.

    Vercel turns each file in /api into its own function, and a detected Python
    framework preset would stop that. So every file here exports a
    BaseHTTPRequestHandler subclass, and nothing in the repo imports anything
    outside the standard library.
    """
    from http.server import BaseHTTPRequestHandler

    files = sorted(n for n in os.listdir(API_DIR) if n.endswith(".py"))
    assert files == ["compile.py", "health.py", "improve.py", "models.py", "preflight.py",
                     "presets.py", "reference.py", "storyboard.py", "video.py"]
    for name in files:
        handler = _load_function(name[:-3])
        assert isinstance(handler, type) and issubclass(handler, BaseHTTPRequestHandler), name

    stdlib = set(sys.stdlib_module_names)
    offenders = []
    # Only the shipped code: /api and promptlayer/. The parsers under scripts/
    # and the tests are dev-only and are excluded from the function bundle, so
    # PyYAML there is fine — the rule is that what deploys has no dependencies.
    for folder in (os.path.join(REPO, "api"), os.path.join(REPO, "promptlayer")):
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "vendor")]
            for name in names:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        match = re.match(r"\s*(?:import|from)\s+([A-Za-z_][\w.]*)", line)
                        if not match:
                            continue
                        top = match.group(1).split(".")[0]
                        if top in stdlib or top == "promptlayer":
                            continue
                        offenders.append(f"{os.path.relpath(path, REPO)}: {line.strip()}")
    assert offenders == [], offenders


def _post(base: str, payload: dict):
    req = urllib.request.Request(
        base + "/",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, json.loads(resp.read().decode())


# ------------------------------------------------------------------ health
def test_health_reports_ok_and_a_model_count():
    base = _server_for("health")
    status, body = _get(base)
    assert status == 200
    assert body["status"] == "ok"
    assert body["models"] >= 10


def _server_for(name: str) -> str:
    """Mount one function on an ephemeral port (used by single-function tests)."""
    handler = _load_function(name)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


# ------------------------------------------------------------------ models
def test_models_lists_everything_the_ui_needs():
    base = _server_for("models")
    status, body = _get(base)
    assert status == 200
    assert len(body["data"]) >= 10
    assert len(body["expanders"]) >= 5
    assert body["version"]
    first = body["data"][0]
    assert {"slug", "display", "provider", "layer_count"} <= set(first)


def test_models_slug_returns_layers_with_sources():
    base = _server_for("models")
    status, body = _get(base, "/?slug=veo3-fast")
    assert status == 200
    detail = body["data"]
    assert detail["provider"] == "fal"
    for layer in detail["layers"]:
        assert layer["source"] and layer["evidence"]


def test_models_unknown_slug_is_a_404_with_the_known_list():
    base = _server_for("models")
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/?slug=not-a-real-model")
    assert exc.value.code == 404
    body = json.loads(exc.value.read().decode())
    assert body["success"] is False
    assert len(body["known"]) >= 10


# ------------------------------------------------------------------ preflight
def test_preflight_flags_the_optimizer_and_offers_the_patch():
    base = _server_for("preflight")
    status, body = _post(base, {"model": "hailuo-02-standard", "prompt": "a cat surfing a wave"})
    assert status == 200
    data = body["data"]
    assert data["prompt"]["rewritten_upstream"] is True
    assert data["full_control_patch"]["prompt_optimizer"] is False
    assert any("prompt_optimizer" in w for w in data["warnings"])


def test_preflight_accepts_an_override_and_clears_the_warning():
    base = _server_for("preflight")
    _, body = _post(base, {
        "model": "hailuo-02-standard",
        "prompt": "a cat surfing a wave",
        "options": {"prompt_optimizer": False},
    })
    assert body["data"]["prompt"]["rewritten_upstream"] is False
    assert body["data"]["params"]["prompt_optimizer"]["source"] == "your override"


def test_preflight_keeps_generic_layers_scoped_to_the_model_kind():
    base = _server_for("preflight")
    _, body = _post(base, {"model": "veo3-fast", "prompt": "a drone shot"})
    data = body["data"]
    active = {l["id"] for l in data["layers"]}
    conditional = {l["id"] for l in data["conditional_layers"]}
    assert "serving.system_message" not in active
    assert "serving.system_message" in conditional


def test_preflight_is_honest_for_an_unknown_model():
    base = _server_for("preflight")
    status, body = _post(base, {"model": "some-future-model", "prompt": "hi"})
    assert status == 200
    assert body["data"]["unknown_model"] is True
    assert body["data"]["params"] == {}
    assert any("No layer facts" in w for w in body["data"]["warnings"])


def test_preflight_rejects_missing_prompt_and_oversized_prompt():
    base = _server_for("preflight")
    for payload in ({"model": "veo3-fast"}, {"model": "veo3-fast", "prompt": "x" * 5000}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, payload)
        assert exc.value.code == 400


# ------------------------------------------------------------------ compile
def test_compile_dry_run_returns_the_verbatim_system_prompt():
    base = _server_for("compile")
    status, body = _post(base, {"prompt": "a cat surfing a wave", "style": "wan-engineer"})
    assert status == 200
    data = body["data"]
    assert data["dry_run"] is True and data["compiled"] is None
    assert "You are a prompt engineer" in data["system_prompt"]
    assert data["origin"].startswith("github.com/Wan-Video")


def test_compile_mirrors_the_single_message_expanders():
    base = _server_for("compile")
    _, body = _post(base, {"prompt": "a cat surfing a wave", "style": "hunyuan-normal"})
    messages = body["data"]["request"]["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert 'input: "a cat surfing a wave"' in messages[0]["content"]


def test_compile_unknown_style_is_a_400_with_the_options():
    base = _server_for("compile")
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, {"prompt": "x", "style": "nope"})
    assert exc.value.code == 400
    body = json.loads(exc.value.read().decode())
    assert len(body["available"]) >= 5


def test_remote_compile_stays_off_without_the_opt_in(monkeypatch):
    """A key alone must not be enough: ALLOW_REMOTE_COMPILE is a deliberate act."""
    monkeypatch.setenv("COMPILE_GATEWAY_URL", "https://example.invalid")
    monkeypatch.setenv("COMPILE_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("ALLOW_REMOTE_COMPILE", raising=False)
    from promptlayer.http import compile_is_enabled

    assert compile_is_enabled() is False
    base = _server_for("compile")
    _, body = _post(base, {"prompt": "a cat surfing a wave", "style": "wan-engineer", "dry_run": False})
    assert body["data"]["dry_run"] is True
    assert "not enabled" in body["data"]["reason"]


# ------------------------------------------------------------------ improve
def test_improve_lints_merges_and_scores_over_http():
    base = _server_for("improve")
    status, body = _post(base, {
        "model": "hailuo-02-standard",
        "prompt": "A beautiful cinematic 8k scene of a man. Camera dollies in and pans left. Make it longer.",
        "selections": {"camera_move": "dolly in", "shot_size": "close-up shot", "tone": "warm colors"},
        "palette": "amber, cream, walnut brown",
        "clean": True,
    })
    assert status == 200
    data = body["data"]
    assert data["after"]["score"] > data["before"]["score"]
    assert data["delta_score"] > 0
    assert "beautiful" not in data["after"]["prompt"]
    assert data["cleaned"]["changes"]
    assert all(c["source"] for c in data["cleaned"]["changes"])
    assert data["design"]["added"]


def test_improve_carries_the_models_own_layers_into_the_lint():
    base = _server_for("improve")
    _, body = _post(base, {"model": "hailuo-02-standard",
                           "prompt": "a cat surfs a wave at dawn, medium shot, slow dolly in, warm light"})
    findings = {f["id"] for f in body["data"]["before"]["findings"]}
    assert "rewriting_layer_active" in findings
    assert body["data"]["before"]["findings"]
    assert all(f["source"] and f["evidence"] for f in body["data"]["before"]["findings"])


def test_improve_uses_only_browser_supplied_image_measurements():
    base = _server_for("improve")
    _, body = _post(base, {
        "model": "sora-2",
        "prompt": "the camera pans up as the man stands, desert at dawn",
        "image": {"count": 1, "width": 1920, "height": 1080, "type": "image/png", "bytes": 400000},
    })
    data = body["data"]
    assert data["image"] == {"count": 1, "width": 1920, "height": 1080, "type": "image/png", "bytes": 400000}
    notes = {n["id"] for n in data["reference_notes"]}
    assert "reference_field" in notes
    assert "reference_size" in notes  # documented size is 720x1280 / 1280x720


def test_improve_rejects_bad_input_and_never_persists_an_image():
    base = _server_for("improve")
    for payload in (
        {"model": "veo3-fast"},
        {"model": "veo3-fast", "prompt": "   "},
        {"model": "veo3-fast", "prompt": "x" * 5000},
        {"prompt": "a cat", "model": ""},
        {"model": "veo3-fast", "prompt": "a cat surfing", "selections": "dolly in"},
        {"model": "veo3-fast", "prompt": "a cat surfing", "template": "haiku"},
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, payload)
        assert exc.value.code == 400, payload
    # an image that fails validation is dropped rather than kept
    _, body = _post(base, {"model": "veo3-fast", "prompt": "a cat surfing at dawn",
                           "image": {"width": 999999, "height": 5, "type": "image/png"}})
    assert body["data"]["image"] == {}


# ------------------------------------------------------------------ storyboard
STORY_CONCEPT = ("A detective named Mara walks through a rain-soaked alley at night. "
                 "Then she finds a glowing book in a doorway and picks it up.")


def test_storyboard_get_serves_the_facets_roles_and_rules():
    base = _server_for("storyboard")
    status, body = _get(base)
    assert status == 200
    assert body["success"] is True
    assert {f["id"] for f in body["facets"]} >= {"subject", "action", "camera_move"}
    assert {r["id"] for r in body["roles"]} == {"first_frame", "last_frame", "style",
                                                "subject", "palette"}
    for rule in body["rules"].values():
        assert rule["source"] and rule["evidence"]


def test_storyboard_plan_is_one_chat_turn():
    base = _server_for("storyboard")
    _, body = _post(base, {"action": "plan", "concept": "a video",
                           "model": "sora-2"})
    data = body["data"]
    assert data["ready"] is False
    assert set(data["blocking_missing"]) == {"subject", "action", "setting"}
    assert data["reply"] and data["questions"]
    for question in data["questions"]:
        assert question["why"] and question["source"] and question["evidence"]


def test_storyboard_scenes_returns_one_prompt_per_beat():
    base = _server_for("storyboard")
    _, body = _post(base, {"action": "scenes", "concept": STORY_CONCEPT,
                           "model": "sora-2", "scene_seconds": 8,
                           "design": {"selections": {"tone": "cool colors"},
                                      "palette": "teal, amber"}})
    data = body["data"]
    assert [s["id"] for s in data["scenes"]] == ["scene-1", "scene-2"]
    assert data["totals"]["per_clip"] == "8"
    assert "Palette anchors: teal, amber" in data["scenes"][0]["prompt"]
    assert data["scenes"][0]["params"]["seconds"] == "8"


def test_storyboard_accepts_a_top_level_palette_like_improve_does():
    """A field the caller sent must not be dropped because of where it sat."""
    base = _server_for("storyboard")
    _, body = _post(base, {"action": "scenes", "concept": STORY_CONCEPT, "model": "sora-2",
                           "palette": "amber, cream, walnut brown",
                           "template": "labelled"})
    data = body["data"]
    assert "Palette anchors: amber, cream, walnut brown" in data["scenes"][0]["prompt"]
    assert data["scenes"][0]["prompt"].startswith("A detective named Mara")


def test_storyboard_accepts_the_callers_own_beats():
    base = _server_for("storyboard")
    _, body = _post(base, {"action": "scenes", "concept": STORY_CONCEPT, "model": "sora-2",
                           "beats": ["only this shot"]})
    assert len(body["data"]["scenes"]) == 1
    assert body["data"]["scenes"][0]["prompt"].startswith("only this shot")


def test_storyboard_rejects_bad_input():
    base = _server_for("storyboard")
    for payload in (
        {"action": "plan"},
        {"action": "plan", "concept": "   "},
        {"action": "plan", "concept": "x" * 9000},
        {"action": "dance", "concept": "a cat walks"},
        {"action": "plan", "concept": "a cat walks", "model": "not-a-model"},
        {"action": "plan", "concept": "a cat walks", "answers": "dolly in"},
        {"action": "scenes", "concept": "a cat walks", "design": "bold"},
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, payload)
        assert exc.value.code == 400, payload


# ------------------------------------------------------------------ reference
def test_reference_get_serves_the_roles_and_the_rules():
    base = _server_for("reference")
    status, body = _get(base)
    assert status == 200
    assert body["max_images"] >= 1
    assert body["rules"] and body["source"] and body["evidence"]


def test_reference_checks_measurements_against_the_endpoint():
    base = _server_for("reference")
    _, body = _post(base, {
        "model": "sora-2",
        "images": [{"name": "frame.png", "role": "last_frame", "width": 1920,
                    "height": 1080, "type": "image/png", "bytes": 1000,
                    "palette": ["#0a1b2c", "#f0e0d0"]}],
    })
    data = body["data"]
    assert data["palette_line"]["line"] == "Palette anchors: #0a1b2c, #f0e0d0"
    ids = {c["id"] for c in data["checks"]}
    assert {"role_not_supported", "reference_size"} <= ids
    for check in data["checks"]:
        assert check["source"] and check["evidence"]
    # Only measurements come back: the image itself is never sent or stored.
    assert set(data["images"][0]) == {"index", "role", "role_asked", "name", "width",
                                      "height", "type", "bytes", "palette", "scene"}


def test_reference_rejects_bad_input():
    base = _server_for("reference")
    for payload in ({"images": "frame.png"}, {"model": "not-a-model", "images": []}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, payload)
        assert exc.value.code == 400, payload


# ------------------------------------------------------------------ video
def test_video_get_reports_the_gate_as_closed(monkeypatch):
    for name in ("ALLOW_REMOTE_GENERATION", "VIDEO_GATEWAY_URL", "VIDEO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    base = _server_for("video")
    status, body = _get(base)
    assert status == 200
    assert body["gate"]["enabled"] is False
    assert "dry run only" in body["gate"]["reason"]


def test_video_plan_is_a_dry_run_with_one_job_per_scene(monkeypatch):
    for name in ("ALLOW_REMOTE_GENERATION", "VIDEO_GATEWAY_URL", "VIDEO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    base = _server_for("video")
    _, body = _post(base, {"action": "plan", "concept": STORY_CONCEPT, "model": "sora-2",
                           "scene_seconds": 8})
    data = body["data"]
    assert data["dry_run"] is True
    assert data["gate"]["enabled"] is False
    assert data["totals"]["requests"] == 2
    assert data["route"] == "POST /v1/videos"
    assert all(job["body"]["prompt"] for job in data["jobs"])


def test_video_submit_refuses_without_the_gate_even_with_confirm(monkeypatch):
    for name in ("ALLOW_REMOTE_GENERATION", "VIDEO_GATEWAY_URL", "VIDEO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    base = _server_for("video")
    _, body = _post(base, {"action": "submit", "concept": STORY_CONCEPT, "model": "sora-2",
                           "scene_seconds": 8, "confirm": True})
    data = body["data"]
    assert data["submitted"] is False
    assert "dry run only" in data["reason"]


def test_video_submit_refuses_without_confirm_when_the_gate_is_open(monkeypatch):
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    base = _server_for("video")
    _, body = _post(base, {"action": "submit", "concept": STORY_CONCEPT, "model": "sora-2",
                           "scene_seconds": 8})
    data = body["data"]
    assert data["submitted"] is False
    assert "confirm=true is required" in data["reason"]


def test_video_accepts_a_storyboard_from_the_other_endpoint():
    base = _server_for("video")
    storyboard = {"scenes": [{"id": "scene-1", "index": 1, "seconds": "8",
                              "prompt": "a lantern drifts down a river at dusk",
                              "params": {"seconds": "8"}, "references": []}]}
    _, body = _post(base, {"action": "plan", "storyboard": storyboard, "model": "sora-2"})
    assert body["data"]["jobs"][0]["body"]["prompt"] == "a lantern drifts down a river at dusk"


def test_video_carries_an_image_into_the_request_built_from_a_concept():
    """The image has to reach the scene, not only the plan around it."""
    base = _server_for("video")
    _, body = _post(base, {
        "action": "plan", "concept": "A lantern drifts down a river at dusk",
        "model": "hailuo-02-i2v",
        "references": [{"name": "frame.png", "role": "first_frame", "width": 1280,
                        "height": 720, "type": "image/png", "bytes": 1000, "palette": []}],
    })
    data = body["data"]
    job = data["jobs"][0]
    assert job["image_fields"] == ["first_frame_image"]
    assert job["body"]["first_frame_image"] == "<attach frame.png (1280x720)>"
    assert data["totals"]["with_image"] == 1
    assert data["reference_set"]["images"][0]["role"] == "first_frame"


def test_video_accepts_a_top_level_palette_and_template_like_storyboard_does():
    base = _server_for("video")
    _, body = _post(base, {"action": "plan", "concept": "A lantern drifts down a river at dusk",
                           "model": "sora-2", "palette": "amber, cream, walnut brown",
                           "template": "labelled"})
    prompt = body["data"]["jobs"][0]["body"]["prompt"]
    assert "Palette anchors: amber, cream, walnut brown" in prompt


def test_video_passes_the_chat_answers_through():
    base = _server_for("video")
    _, body = _post(base, {"action": "plan", "concept": "A lantern drifts down a river at dusk",
                           "model": "sora-2", "answers": {"camera_move": "slow pan left",
                                                          "duration": "8 seconds each"}})
    job = body["data"]["jobs"][0]
    assert "slow pan left" in job["body"]["prompt"]
    assert job["body"]["seconds"] == "8"


def test_video_rejects_bad_input():
    base = _server_for("video")
    for payload in (
        {"action": "plan"},
        {"action": "burn", "concept": "a cat walks"},
        {"action": "plan", "concept": "a cat walks", "model": "not-a-model"},
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(base, payload)
        assert exc.value.code == 400, payload
