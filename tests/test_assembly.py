"""Dataset + assembly tests. No HTTP here — see test_api.py for the functions.

Run:  python -m pytest -q
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from promptlayer import (  # noqa: E402
    build_compile_request,
    build_preflight,
    expander_styles,
    model_list,
    reload_layers,
)


# ---------------------------------------------------------------- data layer
def test_layers_json_is_valid_and_has_sources():
    models = model_list()
    assert len(models) >= 10
    for entry in models:
        assert entry["layer_count"] >= 1, entry


def test_every_layer_claim_carries_a_source_and_evidence():
    for entry in model_list():
        from promptlayer import model_detail

        detail = model_detail(entry["slug"])
        for layer in detail["layers"]:
            assert layer["source"], layer["id"]
            assert layer["evidence"], layer["id"]
            assert layer["kind"] in {"inject", "screen", "rewrite", "expand", "augment", "truncate", "pin"}


def test_no_bare_unix_paths_are_used_as_sources():
    # Sources must be fetchable identifiers (domain or github path), not local files.
    from promptlayer import model_detail

    for entry in model_list():
        for layer in model_detail(entry["slug"])["layers"]:
            src = layer["source"]
            assert "/" in src and not src.startswith(("/", "C:")), src


# ---------------------------------------------------------------- preflight
def test_preflight_flags_the_rewriting_optimizer_on_hailuo():
    result = build_preflight("hailuo-02-standard", "a cat surfing a wave")
    assert result["prompt"]["rewritten_upstream"] is True
    ids = {layer["id"] for layer in result["layers"] if layer["active"]}
    assert "expand.prompt_optimizer" in ids
    warning = " ".join(result["warnings"])
    assert "rewrite" in warning.lower() or "optimizer" in warning.lower()
    assert result["full_control_patch"]["prompt_optimizer"] is False


def test_preflight_respects_an_explicit_override():
    result = build_preflight(
        "hailuo-02-standard", "a cat surfing a wave", options={"prompt_optimizer": False}
    )
    layer = next(x for x in result["layers"] if x["id"] == "expand.prompt_optimizer")
    assert layer["active"] is False
    assert result["params"]["prompt_optimizer"]["value"] is False
    assert result["params"]["prompt_optimizer"]["source"] == "your override"
    assert result["prompt"]["rewritten_upstream"] is False


def test_preflight_reports_veo3_auto_fix_and_safety_tolerance():
    result = build_preflight("veo3-fast", "a drone shot over a city")
    by_id = {layer["id"]: layer for layer in result["layers"]}
    assert by_id["rewrite.auto_fix"]["active"] is True
    assert by_id["screen.safety_tolerance"]["active"] is True
    assert result["params"]["safety_tolerance"]["value"] == "4"
    assert result["full_control_patch"]["auto_fix"] is False


def test_preflight_counts_the_canned_negative_prompt_it_will_attach():
    result = build_preflight("kling-v2.1-master", "a slow pan across a desert")
    layer = next(x for x in result["layers"] if x["id"] == "augment.default_negative")
    assert layer["active"] is True
    assert result["estimate"]["added_chars"] == len("blur, distort, and low quality")
    # The off-switch here is "send your own value", not a boolean to flip, so the
    # warning must say so rather than staying silent about it.
    warning = next(w for w in result["warnings"] if "default_negative" in w)
    assert "send your own negative_prompt" in warning
    # ...and it must not claim a boolean patch exists for it.
    assert "negative_prompt" not in result["full_control_patch"]


def test_preflight_can_replace_a_canned_negative_prompt():
    result = build_preflight(
        "kling-v2.1-master", "a slow pan across a desert",
        options={"negative_prompt": "text overlays, warped hands"},
    )
    layer = next(x for x in result["layers"] if x["id"] == "augment.default_negative")
    assert layer["active"] is False
    assert result["estimate"]["added_chars"] == 0
    assert not [w for w in result["warnings"] if "default_negative" in w]


def test_preflight_notes_ltx_expansion_is_on_and_wan_is_off():
    ltx = build_preflight("ltx-2-19b", "ocean at dawn")
    wan = build_preflight("wan-2.2-a14b", "ocean at dawn")
    assert next(x for x in ltx["layers"] if x["id"] == "expand.prompt_expansion")["active"] is True
    assert next(x for x in wan["layers"] if x["id"] == "expand.prompt_expansion")["active"] is False
    assert ltx["prompt"]["rewritten_upstream"] is True
    assert wan["prompt"]["rewritten_upstream"] is False


def test_preflight_injects_a_system_message_for_chat_models():
    result = build_preflight("gpt-4o-mini", "hello")
    assert result["messages"][0]["role"] == "system"
    assert result["messages"][0]["content"] == "You are a helpful assistant."
    assert any("system message" in w for w in result["warnings"])


def test_preflight_honours_a_custom_system_message():
    result = build_preflight("gpt-4o-mini", "hello", system_message="You are terse.")
    assert result["messages"][0]["content"] == "You are terse."


def test_preflight_scopes_generic_layers_to_the_request_kind():
    # A video request must not be told that a chat system message will be injected.
    video = build_preflight("veo3-fast", "a drone shot")
    video_ids = {layer["id"] for layer in video["layers"]}
    assert "serving.system_message" not in video_ids
    assert "serving.conversation_template" not in video_ids
    # ...and the chat-only layer is reported as not applicable, not silently dropped.
    conditional_ids = {layer["id"] for layer in video["conditional_layers"]}
    assert "serving.system_message" in conditional_ids
    assert all(layer["active"] is None for layer in video["conditional_layers"])

    chat = build_preflight("gpt-4o-mini", "hello")
    chat_ids = {layer["id"] for layer in chat["layers"]}
    assert "serving.system_message" in chat_ids
    assert "provider.safety_checker" not in chat_ids


def test_proxy_behaviour_layers_are_never_counted_as_active():
    result = build_preflight("veo3-fast", "a drone shot")
    active_ids = {layer["id"] for layer in result["layers"] if layer["active"]}
    assert "gateway.context_compression" not in active_ids
    assert "gateway.system_relocation" not in active_ids
    conditional_ids = {layer["id"] for layer in result["conditional_layers"]}
    assert {"gateway.context_compression", "gateway.system_relocation"} <= conditional_ids


def test_preflight_is_honest_about_unknown_models():
    result = build_preflight("some-model-we-never-probed", "hi")
    assert result["unknown_model"] is True
    assert result["params"] == {}
    assert result["full_control_patch"] == {}
    assert any("No layer facts on record" in w for w in result["warnings"])
    # generic layers still apply and are still sourced
    assert all(layer["source"] for layer in result["layers"])


# ---------------------------------------------------------------- compile
def test_compile_uses_the_verbatim_wan_system_prompt():
    built = build_compile_request("a cat surfing a wave", "wan-engineer")
    assert built["request"]["messages"][0]["role"] == "system"
    assert "You are a prompt engineer" in built["system_prompt"]
    assert "80-100 words" in built["system_prompt"]
    assert built["request"]["messages"][1]["content"] == "a cat surfing a wave"


def test_compile_mirrors_the_single_message_expanders():
    built = build_compile_request("a cat surfing a wave", "hunyuan-normal")
    msgs = built["request"]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    assert 'input: "a cat surfing a wave"' in msgs[0]["content"]
    assert built["system_prompt"] is None


def test_compile_unknown_style_raises():
    with pytest.raises(KeyError):
        build_compile_request("x", "not-a-style")


def test_every_expander_declares_its_origin():
    for style in expander_styles():
        assert style["origin"].startswith(("github.com", "raw.githubusercontent.com")), style




def test_reload_keeps_the_dataset_readable():
    data = reload_layers()
    assert data["version"]
    assert data["models"]


def test_dataset_version_is_exposed():
    from promptlayer import dataset_version

    assert dataset_version() == reload_layers()["version"]
