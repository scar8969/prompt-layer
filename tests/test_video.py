"""Tests for the generation layer.

The thing under test is a refusal, not a feature: this module builds the requests a
storyboard becomes and will not send one unless the deployment was explicitly
allowed to spend. A key on its own is deliberately not enough.

Run:  python -m pytest -q
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from promptlayer import (  # noqa: E402
    build_plan,
    build_scenes,
    generation_gate,
    model_detail,
    submit_plan,
)
from promptlayer import video as video_mod  # noqa: E402

CONCEPT = ("A detective named Mara walks through a rain-soaked alley at night. "
           "Then she finds a glowing book in a doorway and picks it up.")


@pytest.fixture(autouse=True)
def _no_ambient_gate(monkeypatch):
    """No test inherits a gate from the environment it happens to run in."""
    for name in ("ALLOW_REMOTE_GENERATION", "VIDEO_GATEWAY_URL", "VIDEO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield


def _storyboard(model="sora-2"):
    return build_scenes(CONCEPT, model=model, model_detail=model_detail(model),
                        scene_seconds=8)


# ----------------------------------------------------------------- the gate
def test_nothing_is_enabled_by_default():
    gate = generation_gate()
    assert gate["enabled"] is False
    assert "dry run only" in gate["reason"]
    assert set(gate["missing"]) == {"ALLOW_REMOTE_GENERATION=1", "VIDEO_GATEWAY_URL",
                                    "VIDEO_API_KEY"}


def test_a_key_alone_does_not_enable_spending(monkeypatch):
    """The whole point: configuring a key must not silently enable paid calls."""
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    gate = generation_gate()
    assert gate["enabled"] is False
    assert gate["missing"] == ["ALLOW_REMOTE_GENERATION=1"]


def test_the_opt_in_needs_all_three(monkeypatch):
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    assert generation_gate()["enabled"] is False
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    assert generation_gate()["enabled"] is False
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    gate = generation_gate()
    assert gate["enabled"] is True
    assert gate["missing"] == []
    assert "explicitly allowed" in gate["reason"]


# ----------------------------------------------------------------- the plan
def test_one_job_per_scene_with_the_endpoints_own_body():
    plan = build_plan(_storyboard(), model_detail("sora-2"))
    assert plan["totals"]["requests"] == 2
    assert plan["totals"]["clips"] == 2
    assert plan["dry_run"] is True
    assert plan["gate"]["enabled"] is False
    for job in plan["jobs"]:
        assert job["method"] == "POST"
        assert job["body"]["prompt"]
        assert job["body"]["seconds"] == "8"
        assert "negative_prompt" not in job["body"]


def test_the_route_is_read_out_of_the_dataset_not_normalised():
    """sora's entry carries a route; a fal entry carries an endpoint id."""
    plan = build_plan(_storyboard("sora-2"), model_detail("sora-2"))
    assert plan["route"] == "POST /v1/videos"
    assert plan["jobs"][0]["path"] == "/v1/videos"

    fal = build_plan(_storyboard("hailuo-02-i2v"), model_detail("hailuo-02-i2v"))
    assert fal["route"].startswith("POST fal-ai/")
    assert any(n["id"] == "endpoint_id_not_route" for n in fal["notes"])


def test_a_scene_that_uses_a_reference_gets_a_named_image_field():
    refs = [{"index": 1, "role": "first_frame", "name": "frame.png",
             "width": 1280, "height": 720, "type": "image/png", "bytes": 100,
             "palette": [], "scene": 1}]
    storyboard = build_scenes("A lantern drifts down a river at dusk", model="hailuo-02-i2v",
                              model_detail=model_detail("hailuo-02-i2v"), references=refs)
    plan = build_plan(storyboard, model_detail("hailuo-02-i2v"), references=refs)
    job = plan["jobs"][0]
    assert job["image_fields"] == ["first_frame_image"]
    # The pixels never reach this service, so the field is a placeholder the user fills.
    assert job["body"]["first_frame_image"] == "<attach frame.png (1280x720)>"
    assert plan["totals"]["with_image"] == 1


def test_the_prompt_rule_is_quoted_with_the_plan():
    plan = build_plan(_storyboard(), model_detail("sora-2"))
    note = next(n for n in plan["notes"] if n["id"] == "one_request_per_scene")
    assert "2 request(s), one per scene" == note["title"]
    assert "Generate each part as a separate clip" in note["evidence"]


def test_an_empty_storyboard_plans_nothing():
    plan = build_plan({"scenes": []}, model_detail("sora-2"))
    assert plan["jobs"] == []
    assert plan["totals"]["requests"] == 0


# ----------------------------------------------------------------- the refusal
def test_submit_refuses_without_the_gate():
    result = submit_plan(build_plan(_storyboard(), model_detail("sora-2")), confirm=True)
    assert result["submitted"] is False
    assert "dry run only" in result["reason"]


def test_submit_refuses_without_confirm(monkeypatch):
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    result = submit_plan(build_plan(_storyboard(), model_detail("sora-2")))
    assert result["submitted"] is False
    assert "confirm=true is required" in result["reason"]


def test_submit_refuses_an_empty_plan(monkeypatch):
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    result = submit_plan({"jobs": []}, confirm=True)
    assert result["submitted"] is False
    assert "no jobs" in result["reason"]


def test_a_gated_submit_sends_the_planned_bodies(monkeypatch):
    """With the gate open and confirm set, the requests go out as planned."""
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example/")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")
    sent = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"id": "vid_1"}).encode()

    def fake_urlopen(request, timeout=None):
        sent.append({"url": request.full_url, "method": request.get_method(),
                     "auth": request.headers.get("Authorization"),
                     "body": json.loads(request.data.decode())})
        return FakeResponse()

    monkeypatch.setattr(video_mod.urllib.request, "urlopen", fake_urlopen)
    plan = build_plan(_storyboard(), model_detail("sora-2"))
    result = submit_plan(plan, confirm=True)
    assert result["submitted"] is True
    assert len(sent) == 2 == len(result["results"])
    assert sent[0]["url"] == "https://gateway.example/v1/videos"
    assert sent[0]["auth"] == "Bearer sk-test-not-a-real-key"
    assert sent[0]["body"]["prompt"].startswith("A detective named Mara")
    assert all(r["status"] == 200 for r in result["results"])


def test_an_upstream_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("ALLOW_REMOTE_GENERATION", "1")
    monkeypatch.setenv("VIDEO_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_API_KEY", "sk-test-not-a-real-key")

    def boom(request, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr(video_mod.urllib.request, "urlopen", boom)
    result = submit_plan(build_plan(_storyboard(), model_detail("sora-2")), confirm=True)
    assert result["submitted"] is True
    assert all(r["status"] is None for r in result["results"])
    assert "connection reset" in result["results"][0]["error"]
