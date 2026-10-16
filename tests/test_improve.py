"""Tests for the improvement layer: the lint rules and the design merge.

Each check in design.json must be reachable and must stay quiet when the prompt
already does the right thing — a lint that fires on everything is worthless.

Run:  python -m pytest -q
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from promptlayer import (  # noqa: E402
    apply_design,
    build_improve,
    check_selection_conflicts,
    clean_prompt,
    lint_prompt,
    load_design,
    vocab,
)


def ids(findings):
    return {f["id"] for f in findings}


# ------------------------------------------------------------------ the dataset
def test_every_check_cites_a_known_source_with_evidence():
    design = load_design()
    for check in design["checks"]:
        assert check["source"] in design["sources"], check["id"]
        assert check["evidence"], check["id"]
        assert check["severity"] in ("error", "warn", "info")


def test_every_vocabulary_item_cites_a_known_source_with_evidence():
    design = load_design()
    for key, group in vocab().items():
        assert group["source"] in design["sources"], key
        assert group["evidence"], key
        assert group["items"], key


# ------------------------------------------------------------------ the lint
def test_a_weak_prompt_trips_the_rules_it_should():
    weak = ('A beautiful, stunning 8k cinematic scene of a man. He walks across the room, then he '
            'opens a door, then he sits down. Camera dollies in and pans left. '
            'The man says "I have been waiting for this moment for a very long time and I am tired." '
            "Don't show any text. Make it longer.")
    result = lint_prompt(weak)
    fired = ids(result["findings"])
    assert {"slop_adjectives", "params_in_prose", "multi_shot", "camera_conflict",
            "quoted_dialogue", "dialogue_too_long", "negatives_in_prose"} <= fired
    assert result["counts"]["error"] >= 1
    assert result["score"] < 60


def test_a_well_written_prompt_is_left_alone():
    good = ("Dawn light through a workshop window, warm key from a single bulb. A small round robot "
            "sits on a bench and taps a bulb twice; sparks crackle and it flinches. "
            "Medium close-up, 35mm lens, shallow depth of field, slow dolly in. "
            "Palette anchors: amber, cream, walnut brown. Diegetic sound only: rain, ticking clock.")
    result = lint_prompt(good)
    fired = ids(result["findings"])
    assert "slop_adjectives" not in fired
    assert "camera_conflict" not in fired
    assert "no_lighting" not in fired
    assert "no_motion" not in fired
    assert "quoted_dialogue" not in fired
    assert result["score"] >= 85


def test_cinematic_is_only_flagged_without_a_technical_reason():
    bare = lint_prompt("a cinematic shot of a car driving")
    assert "bare_cinematic" in ids(bare["findings"])
    justified = lint_prompt("a cinematic anamorphic shot on 35mm film of a car driving, dusk light")
    assert "bare_cinematic" not in ids(justified["findings"])


def test_opposing_camera_moves_are_an_error():
    result = lint_prompt("a slow dolly in while the camera dollies out, a cat sits still in the rain")
    finding = next(f for f in result["findings"] if f["id"] == "camera_conflict")
    assert finding["severity"] == "error"
    assert "dolly in + dolly out" in finding["detail"]


def test_motion_check_fires_on_a_still_life():
    still = lint_prompt("A weathered typewriter on a wooden desk, soft light, warm palette, medium shot")
    assert "no_motion" in ids(still["findings"])


def test_length_check_both_ends():
    assert "length" in ids(lint_prompt("a cat")["findings"])
    assert "length" in ids(lint_prompt(" ".join(["word"] * 200))["findings"])


def test_rewriting_layer_is_reported_as_double_prompting():
    layers = [{"id": "expand.prompt_optimizer", "kind": "expand", "active": True,
               "disable_with": {"prompt_optimizer": False}, "source": "x", "evidence": "y"}]
    result = lint_prompt("a cat surfs a wave at dawn, medium shot, warm light, slow dolly in", model_layers=layers)
    finding = next(f for f in result["findings"] if f["id"] == "rewriting_layer_active")
    assert "prompt_optimizer=false" in finding["fix"]
    # and not reported when the layer is off
    layers[0]["active"] = False
    assert "rewriting_layer_active" not in ids(
        lint_prompt("a cat surfs a wave at dawn, slow dolly in", model_layers=layers)["findings"])


def test_image_rules_depend_on_whether_the_endpoint_takes_one():
    prompt = "the camera pans up as the man stands, desert at dawn, warm light"
    with_image = ids(lint_prompt(prompt, image_count=1, accepts_image=True)["findings"])
    assert "image_reference_rules" in with_image
    without_support = ids(lint_prompt(prompt, image_count=1, accepts_image=False)["findings"])
    assert "image_not_supported" in without_support


# ------------------------------------------------------------------ the merge
def test_apply_design_merges_in_a_documented_order_and_reports_sources():
    result = apply_design("A robot sits on a bench and taps a bulb twice.",
                          {"camera_move": "dolly in", "shot_size": "close-up shot",
                           "light_quality": "soft lighting", "tone": "warm colors",
                           "negative": ["text overlays", "warped hands"]},
                          template="inline")
    text = result["prompt"]
    assert text.startswith("A robot sits on a bench")
    assert "Camera: dolly in" in text
    assert "Shot: close-up shot" in text
    assert "Negative prompt: text overlays, warped hands" in text
    assert result["added"] and all(item["source"] for item in result["added"])
    assert result["unknown"] == []


def test_labelled_template_produces_the_documented_blocks():
    result = apply_design("A robot sits on a bench.",
                          {"camera_move": "dolly in", "light_source": "moonlight"},
                          template="labelled")
    assert "Cinematography:" in result["prompt"]
    assert "Camera shot: dolly in" in result["prompt"]
    assert "Lighting + palette: moonlight" in result["prompt"]


def test_apply_design_refuses_values_it_cannot_source():
    result = apply_design("A robot sits.", {"camera_move": "interpretive dance"})
    assert result["unknown"] == ["camera_move=interpretive dance"]
    assert "interpretive dance" not in result["prompt"]
    assert result["added"] == []


def test_an_unknown_template_falls_back_and_says_so():
    """The merge reports a value it cannot place rather than raising: a raise here
    reaches the client as a 502 with no explanation, and /api/improve already
    answers 400 for a template it does not document before calling this."""
    result = apply_design("A robot sits.", {}, template="haiku")
    assert result["template"] == "inline"
    assert result["unknown"] == ["template=haiku"]
    assert "A robot sits." in result["prompt"]
    # A template that is documented is still used, and is not reported.
    labelled = apply_design("A robot sits.", {}, template="labelled")
    assert labelled["template"] == "labelled" and labelled["unknown"] == []
    # A non-string is the same case as an unknown one, not a crash.
    assert apply_design("A robot sits.", {}, template=7)["template"] == "inline"
    assert apply_design("A robot sits.", {}, template=None)["unknown"] == []


def test_dialogue_is_written_as_a_block_not_as_a_design_element():
    """The line is the user's own words, so it is written verbatim and sourced."""
    inline = apply_design("A robot sits.", {"dialogue": "Mara: it is raining"}, template="inline")
    assert "Dialogue: Mara: it is raining." in inline["prompt"]
    labelled = apply_design("A robot sits.", {"dialogue": "Mara: it is raining"}, template="labelled")
    assert "Dialogue: Mara: it is raining" in labelled["prompt"]
    element = next(e for e in labelled["added"] if e["element"] == "dialogue")
    assert element["value"] == "Mara: it is raining"
    assert element["source"] and element["evidence"]


def test_no_dialogue_means_no_dialogue_block():
    """An empty answer must not leave an empty block behind."""
    result = apply_design("A robot sits.", {"dialogue": "   "}, template="labelled")
    assert "Dialogue:" not in result["prompt"]
    assert not any(e["element"] == "dialogue" for e in result["added"])


def test_palette_is_free_text_and_still_sourced():
    result = apply_design("A robot sits quietly on a bench.", palette="amber, cream, walnut brown")
    assert "Palette anchors: amber, cream, walnut brown" in result["prompt"]
    item = next(a for a in result["added"] if a["element"] == "palette")
    assert item["source"]


# ------------------------------------------------------------------ the whole operation
def test_build_improve_scores_before_and_after():
    weak = "A beautiful cinematic scene of a man walking in a room, 8k, make it longer"
    result = build_improve(
        weak,
        selections={"camera_move": "dolly in", "shot_size": "medium shot",
                    "light_source": "daylight", "tone": "warm colors"},
        template="inline",
        palette="amber, cream, walnut",
    )
    assert result["before"]["score"] < result["after"]["score"]
    assert result["delta_score"] > 0
    assert result["design"]["added"]
    assert result["version"]
    # The weak words are still in the prose — the merge does not silently rewrite it.
    assert "beautiful" in result["after"]["prompt"]
    assert "slop_adjectives" in {f["id"] for f in result["after"]["findings"]}


def test_build_improve_reports_the_rewriting_layer_from_the_model():
    layers = [{"id": "expand.prompt_optimizer", "kind": "expand", "active": True,
               "disable_with": {"prompt_optimizer": False}, "source": "x", "evidence": "y"}]
    result = build_improve("a cat surfs a wave at dawn, slow dolly in", model_layers=layers)
    assert "rewriting_layer_active" in {f["id"] for f in result["before"]["findings"]}


# ------------------------------------------------------------------ the cleanup
def test_clean_removes_slop_and_prose_parameters_and_says_what_it_removed():
    result = clean_prompt("A beautiful stunning 8k cinematic scene of a man, make it longer.")
    text = result["prompt"]
    for gone in ("beautiful", "stunning", "8k", "make it longer"):
        assert gone not in text
        assert any(gone in c["removed"] for c in result["changes"])
    assert "cinematic scene of a man" in text


def test_clean_unquotes_dialogue_and_keeps_the_words():
    result = clean_prompt('The detective says "You are lying."')
    assert result["prompt"] == "The detective says You are lying."
    assert any("You are lying" in c["removed"] for c in result["changes"])


def test_clean_keeps_exactly_one_camera_move_preferring_the_chosen_one():
    result = clean_prompt("Camera dollies in and pan left across the street.", keep_move="pan left")
    text = result["prompt"]
    assert "pan left" in text
    assert "doll" not in text.lower()
    assert any(c["removed"] == "dolly in" for c in result["changes"])


def test_clean_leaves_a_good_prompt_untouched():
    good = ("Dawn light through a workshop window. A small robot taps a bulb twice and flinches. "
            "Medium close-up, 35mm lens, slow dolly in, warm key from a practical bulb.")
    result = clean_prompt(good)
    assert result["changes"] == []
    assert result["prompt"].strip() == good.strip()


def test_clean_then_merge_moves_the_score_further_than_merging_alone():
    weak = "A beautiful cinematic 8k scene of a man. Camera dollies in and pans left. Make it longer."
    selections = {"camera_move": "dolly in", "shot_size": "medium shot", "tone": "warm colors"}
    merge_only = build_improve(weak, selections=selections)
    cleaned = build_improve(weak, selections=selections, clean=True)
    assert cleaned["delta_score"] > merge_only["delta_score"]
    assert cleaned["cleaned"]["changes"]
    assert "beautiful" not in cleaned["after"]["prompt"]
    assert "8k" not in cleaned["after"]["prompt"]


def test_conflicts_flag_a_second_camera_move_before_it_is_added():
    conflicts = check_selection_conflicts("a slow pan left across the desert", {"camera_move": "dolly in"})
    assert conflicts and conflicts[0]["id"] == "camera_move_conflict"
    assert "already has a camera move" in conflicts[0]["detail"]
    # picking the move the prompt already has is not a conflict
    assert check_selection_conflicts("a slow pan left across the desert", {"camera_move": "pan left"}) == []
