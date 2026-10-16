"""Tests for the scene planner: the reading, the questions, and the split.

The rule this file exists to defend is the same one the rest of the project lives
by: the planner reads what you wrote and reports what it found. It never fills a
gap with a guess, and every rule it applies is quoted from the model owner that
published it.

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
    build_scenes,
    facet_catalog,
    model_detail,
    plan,
    planner_rules,
    read_facets,
    role_catalog,
    split_beats,
    storyboard_version,
)
from promptlayer import storyboard as sb_mod  # noqa: E402

CONCEPT = (
    "A detective named Mara walks through a rain-soaked alley at night. "
    "Then she finds a glowing book in a doorway and picks it up. "
    "Finally she runs across a neon bridge, 8 seconds each, 16:9, moonlight."
)


# ----------------------------------------------------------------- the dataset
def test_every_rule_carries_a_fetchable_source_and_verbatim_evidence():
    rules = planner_rules()
    assert {"one_beat_per_clip", "storyboard_first", "shot_specific",
            "character_reference", "extension"} <= set(rules)
    for rule_id, rule in rules.items():
        assert rule["source"] and ("/" in rule["source"] or "github.com" in rule["source"]), rule_id
        assert rule["evidence"] and len(rule["evidence"]) > 40, rule_id
        assert rule["why"] and rule["fix"], rule_id


def test_the_split_rule_quotes_the_page_it_comes_from():
    """The one rule the whole scene-splitting feature rests on."""
    rule = planner_rules()["one_beat_per_clip"]
    assert "muddled or incomplete videos" in rule["evidence"]
    assert "Generate each part as a separate clip" in rule["evidence"]
    assert rule["source"].endswith("video/best-practice")


def test_every_facet_is_sourced_and_says_why_the_model_needs_it():
    facets = facet_catalog()
    ids = {f["id"] for f in facets}
    assert {"subject", "action", "setting", "camera_move", "duration", "aspect"} <= ids
    for facet in facets:
        assert facet["why"], facet["id"]
        assert facet["source"], facet["id"]
        assert facet["evidence"], facet["id"]
        assert isinstance(facet["blocking"], bool)
    # Options for the camera facet come from the same vocabulary the lint uses,
    # so a value the planner offers can never be one the merge would reject.
    camera = next(f for f in facets if f["id"] == "camera_move")
    assert "dolly in" in camera["options"]
    duration = next(f for f in facets if f["id"] == "duration")
    # A facet names the parameters that can carry it, in the order to try, so
    # an endpoint that documents 'duration' rather than 'seconds' is still
    # asked about its own values.
    assert duration["options_from_params"] == ["seconds", "duration"]


def test_the_cue_words_are_labelled_as_a_heuristic_not_a_published_rule():
    cues = sb_mod.load_storyboard()["cues"]
    assert cues["sourced"] is False
    assert "heuristic" in cues["note"]
    assert cues["patterns"]


def test_reference_roles_are_sourced():
    roles = role_catalog()
    ids = {r["id"] for r in roles}
    assert {"first_frame", "last_frame", "style", "subject", "palette"} == ids
    for role in roles:
        assert role["source"] and role["evidence"], role["id"]


# ----------------------------------------------------------------- the reading
def test_the_reading_finds_what_is_there():
    found = read_facets(CONCEPT)["found"]
    assert found["subject"] == ["Mara"]
    assert "walks" in found["action"] and "runs" in found["action"]
    assert "rain-soaked alley" in found["setting"]
    assert found["light_source"] == ["moonlight"]
    assert found["duration"] == ["8"]
    assert found["aspect"] == ["16:9"]


def test_the_reading_invents_nothing():
    """An empty concept has no facets, and says so rather than guessing one."""
    reading = read_facets("")
    assert reading["found"] == {}
    assert reading["ids"] == []
    assert "no model involved" in reading["note"]

    vague = read_facets("something happens, it is nice")["found"]
    assert "subject" not in vague
    assert "camera_move" not in vague


def test_the_reading_tolerates_inflected_camera_moves():
    """"dollies in" is how people write "dolly in"."""
    assert read_facets("the camera dollies in on her face")["found"]["camera_move"] == ["dolly in"]


def test_the_setting_reading_stops_at_the_clause():
    """A setting pulled out of prose trails into the next clause; it must not."""
    found = read_facets("she waits in the doorway and then she leaves")["found"]
    assert found["setting"] == ["doorway"]


# ----------------------------------------------------------------- the chat turn
def test_a_bare_concept_asks_only_for_what_is_blocking():
    result = plan("a video", model_detail=model_detail("sora-2"))
    assert result["ready"] is False
    assert set(result["blocking_missing"]) == {"subject", "action", "setting"}
    asked = [q["facet"] for q in result["questions"] if q["blocking"]]
    assert set(asked) == {"subject", "action", "setting"}
    for question in result["questions"]:
        assert question["why"] and question["source"] and question["evidence"]
    assert len(result["questions"]) <= sb_mod.MAX_QUESTIONS
    assert "Still missing" in result["reply"]


def test_a_question_does_not_repeat_its_own_options():
    """The options come back as buttons; the sentence stays a sentence."""
    result = plan("a video")
    asked = [q for q in result["questions"] if q["options"]]
    assert asked, "a bare concept should offer options somewhere"
    for question in asked:
        for option in question["options"]:
            assert option not in question["question"]


def test_a_request_phrasing_is_not_read_as_a_subject():
    """"a video about a cat" is a request; the subject is the cat, not the wrapper."""
    found = read_facets("a video about a cat")["found"]
    assert found.get("subject") == ["cat"]
    assert read_facets("a clip of a boy running")["found"]["subject"] == ["boy"]


def test_a_question_offers_the_endpoints_own_values():
    """The clip-length question offers what this endpoint documents, not a list we made up."""
    result = plan("a cat walks along a wall", model_detail=model_detail("sora-2"))
    duration = next(q for q in result["questions"] if q["facet"] == "duration")
    assert duration["options"] == ["4", "8", "12", "16", "20"]
    assert "sora-2" in duration["source"] or "sora" in duration["source"].lower()


def test_answers_fill_the_gaps_and_nothing_else():
    result = plan("a cat", answers={"subject": "a tabby cat", "action": "walks"},
                  model_detail=model_detail("sora-2"))
    assert "subject" not in result["blocking_missing"]
    assert result["found"]["subject"] == ["a tabby cat"]
    # "setting" was still not answered, so it is still blocking.
    assert result["blocking_missing"] == ["setting"]


def test_a_complete_concept_is_ready():
    result = plan(CONCEPT, model_detail=model_detail("sora-2"))
    assert result["ready"] is True
    assert result["blocking_missing"] == []
    assert "Nothing blocking left" in result["reply"] or "Ready" in result["reply"]


def test_several_events_in_one_prompt_is_flagged_with_the_published_rule():
    result = plan(CONCEPT, model_detail=model_detail("sora-2"))
    warning = next(w for w in result["warnings"] if w["id"] == "multiple_beats")
    assert "3 shots" in warning["title"]
    assert "Generate each part as a separate clip" in warning["evidence"]


def test_two_camera_moves_in_one_beat_is_flagged():
    result = plan("the camera pans left then dollies in on the door",
                  model_detail=model_detail("sora-2"))
    assert any(w["id"] == "many_moves" for w in result["warnings"])


# ----------------------------------------------------------------- the split
def test_one_sentence_one_beat_and_the_cue_is_not_left_in_the_prompt():
    beats = split_beats(CONCEPT)
    assert len(beats) == 3
    assert beats[0]["text"].startswith("A detective named Mara")
    # "Then" and "Finally" are boundary markers, not content.
    assert beats[1]["text"].startswith("she finds")
    assert beats[2]["text"].startswith("she runs")


def test_a_short_fragment_is_merged_rather_than_made_into_a_shot():
    beats = split_beats("he opens the door. Then he leaves.")
    assert len(beats) == 2
    assert beats[1]["text"].rstrip(".") == "he leaves"


def test_a_stub_cut_out_of_a_sentence_joins_the_beat_it_came_from():
    """A one-word leftover is not a shot of its own."""
    beats = split_beats("she walks in and then sits")
    assert len(beats) == 1
    assert beats[0]["text"].endswith("sits")


def test_two_real_actions_in_one_sentence_become_two_beats():
    beats = split_beats("she walks in and then sits down by the window")
    assert [b["text"] for b in beats] == ["she walks in", "sits down by the window"]


def test_the_split_is_capped():
    assert len(split_beats("one shot. " * 60)) <= sb_mod.MAX_BEATS


# ----------------------------------------------------------------- the scenes
def test_one_scene_per_beat_with_the_endpoints_own_parameters():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    assert result["totals"]["clips"] == 3
    assert [s["id"] for s in result["scenes"]] == ["scene-1", "scene-2", "scene-3"]
    for scene in result["scenes"]:
        assert scene["params"]["seconds"] == "8"
        assert scene["prompt"].strip()
        assert scene["score"] > 0


def test_the_stated_clip_length_is_used_and_snapped_to_a_documented_value():
    """The concept says 8 seconds; sora documents 4/8/12/16/20, so 8 is used as-is."""
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    assert result["totals"]["per_clip"] == "8"
    assert result["totals"]["per_clip_asked"] == 8.0
    assert result["totals"]["documented_lengths"] == ["4", "8", "12", "16", "20"]

    # Ask for a length this endpoint does not document and it snaps down, loudly.
    odd = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                       scene_seconds=7)
    assert odd["totals"]["per_clip"] == "4"
    assert any(n["id"] == "clip_length" for n in odd["notes"])


def test_a_parameter_this_endpoint_does_not_document_is_never_sent():
    """16:9 is not a value sora's `size` accepts: it documents 720x1280 or 1280x720."""
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    assert result["aspect"] == "16:9"
    for scene in result["scenes"]:
        assert "size" not in scene["params"]
    rejected = result["param_notes"]
    assert rejected and rejected[0]["parameter"] == "size"
    assert rejected[0]["asked"] == "16:9"
    assert rejected[0]["documented"] == ["720x1280", "1280x720"]
    assert "Left out of the request" in rejected[0]["detail"]


def test_parameters_asked_for_in_prose_are_removed_and_reported():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    last = result["scenes"][2]
    assert "8 seconds" not in last["prompt"]
    assert "16:9" not in last["prompt"]
    removed = [c["removed"] for c in last["cleaned"]["changes"]]
    assert "8 seconds each" in removed
    assert "16:9" in removed


def test_the_design_set_is_merged_but_never_twice():
    """A design element the beat already says is not added again."""
    design = {"selections": {"tone": "cool colors", "camera_move": "dolly in"},
              "palette": "teal, amber"}
    result = build_scenes("she walks through the rain, the camera dollies in",
                          model="sora-2", model_detail=model_detail("sora-2"), design=design)
    scene = result["scenes"][0]
    added = {a["element"] for a in scene["added"]}
    # The beat already carries the move, so the design set does not restate it.
    assert "camera_move" not in added
    assert scene["prompt"].count("dollies in") == 1
    assert "Camera:" not in scene["prompt"]
    assert "cool colors" in scene["prompt"]
    assert "Palette anchors: teal, amber" in scene["prompt"]
    assert "tone" in added


def test_a_design_value_the_vocabulary_does_not_have_is_reported_not_merged():
    """The merge never invents: a value outside the sourced vocabulary is surfaced."""
    design = {"selections": {"tone": "cool colours"}}   # British spelling
    result = build_scenes("a fox crosses a field", model="sora-2",
                          model_detail=model_detail("sora-2"), design=design)
    scene = result["scenes"][0]
    assert "cool colours" not in scene["prompt"]
    assert any("tone=cool colours" == item for item in scene["unknown"])


def test_every_merged_element_carries_its_source():
    design = {"selections": {"light_source": "moonlight"}, "palette": "teal"}
    result = build_scenes("a fox crosses a field", model="sora-2",
                          model_detail=model_detail("sora-2"), design=design)
    for element in result["scenes"][0]["added"]:
        assert element["source"] and element["evidence"], element


def test_the_storyboard_reports_the_generations_it_becomes():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    note = next(n for n in result["notes"] if n["id"] == "one_beat_per_clip")
    assert "3 scene(s) → 3 generation(s)" == note["title"]
    assert "Generate each part as a separate clip" in note["evidence"]


def test_continuity_uses_one_fixed_wording_per_subject():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"))
    handles = result["continuity"]["subjects"]
    assert handles == [{"handle": "@mara", "wording": "Mara"}]
    assert "same words each time" in result["continuity"]["lines"][0]
    # The same line is repeated on every scene, which is the point.
    for scene in result["scenes"]:
        assert scene["continuity"] == result["continuity"]["lines"]


def test_an_empty_concept_produces_no_scenes_rather_than_a_blank_one():
    result = build_scenes("   ", model="sora-2", model_detail=model_detail("sora-2"))
    assert result["scenes"] == []
    assert result["totals"]["clips"] == 0


def test_scenes_work_without_a_model_and_say_so():
    result = build_scenes(CONCEPT)
    assert result["scenes"] and result["model"] is None
    assert any(n["id"] == "no_model" for n in result["notes"])


def test_the_version_is_surfaced():
    assert storyboard_version() == json.load(
        open(os.path.join(REPO, "promptlayer", "storyboard.json"), encoding="utf-8"))["version"]


# ------------------------------------------------- what the chat answers become
def test_an_answer_for_a_documented_group_is_merged_into_every_scene():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          answers={"camera_move": "dolly in", "tone": "warm colors"})
    for scene in result["scenes"]:
        assert "dolly in" in scene["prompt"]
        assert result["answers_placed"]["camera_move"] == "dolly in"
    elements = {e["element"] for e in result["scenes"][0]["added"]}
    assert {"camera_move", "tone"} <= elements


def test_an_answer_outside_the_documented_vocabulary_is_reported_not_merged():
    """Same rule as the design panel: an unsourced value never reaches the prompt."""
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          answers={"tone": "tense"})
    assert "tense" not in result["scenes"][0]["prompt"]
    assert "tone=tense" in result["scenes"][0]["unknown"]


def test_an_answer_for_a_parameter_the_endpoint_documents_becomes_that_parameter():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          answers={"duration": "8 seconds each", "aspect": "16x9"})
    assert result["totals"]["per_clip"] == "8"
    assert result["aspect"] == "16x9"
    assert all(s["params"]["seconds"] == "8" for s in result["scenes"])


def test_an_answer_that_is_neither_is_reported_not_dropped():
    """The chat answer must not vanish: if it cannot be placed, say so."""
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          answers={"subject": "a second detective"})
    assert result["answers_unplaced"] == ["subject"]
    note = next(n for n in result["notes"] if n["id"] == "answer_not_placed")
    assert "subject" in note["detail"]
    assert "concept prose" in note["detail"]
    assert note["source"] and note["evidence"]


def test_dialogue_lands_in_the_documented_block():
    result = build_scenes("a woman waits at a window", model="sora-2",
                          model_detail=model_detail("sora-2"),
                          answers={"dialogue": "Mara: it is raining again"},
                          design={"template": "labelled"})
    prompt = result["scenes"][0]["prompt"]
    assert "Dialogue: Mara: it is raining again" in prompt
    # The documented block order puts Dialogue after the prose blocks.
    assert prompt.index("Dialogue:") > prompt.index("Cinematography:")
    element = next(e for e in result["scenes"][0]["added"] if e["element"] == "dialogue")
    assert element["source"] and element["evidence"]


def test_a_design_selection_wins_over_a_chat_answer():
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          design={"selections": {"camera_move": "static shot"}},
                          answers={"camera_move": "dolly in"})
    assert "static shot" in result["scenes"][0]["prompt"]
    assert "dolly in" not in result["scenes"][0]["prompt"]


def test_the_plan_and_the_scenes_agree_about_what_an_answer_fills():
    """A gap the chat fills must not be asked again, and must reach the prompt."""
    answers = {"camera_move": "slow pan left"}
    after = plan(CONCEPT, answers=answers, model_detail=model_detail("sora-2"))
    assert not any(q["facet"] == "camera_move" for q in after["questions"])
    result = build_scenes(CONCEPT, model="sora-2", model_detail=model_detail("sora-2"),
                          answers=answers)
    assert "slow pan left" in result["scenes"][0]["prompt"]


# ------------------------------------------------- the model you are discussing
def test_the_endpoint_you_are_discussing_decides_the_answer_options():
    """Two endpoints document different clip lengths, so the same concept is
    asked about differently — which is what makes the discussion model a real
    choice rather than a label."""
    concept = "a lighthouse keeper walks out onto the pier at dawn"

    def asked(slug):
        result = plan(concept, model_detail=model_detail(slug))
        return next(q for q in result["questions"] if q["facet"] == "duration")

    sora, veo, hailuo = asked("sora-2"), asked("veo3-fast"), asked("hailuo-02-i2v")
    assert sora["options"] == ["4", "8", "12", "16", "20"]
    assert veo["options"] == ["8s"]
    assert hailuo["options"] == ["6"]
    # Each question says which endpoint's parameter the options came from.
    assert veo["options_from"]["parameter"] == "duration"
    assert sora["options_from"]["parameter"] == "seconds"
    assert "Veo 3 Fast" in veo["source"]
    # The rule the question rests on is still the published one, with its quote.
    assert veo["evidence"] == sora["evidence"]
    assert "/" in veo["source"].replace(" ", "-") or veo["options_from"]


def test_an_endpoint_that_documents_nothing_for_a_facet_offers_no_options():
    """No options and no invented ones: the facet's own published vocabulary is
    used only when it has one, and otherwise the question is free text."""
    result = plan("a kite over a field", model_detail=model_detail("wan-2.2-a14b-i2v"))
    duration = next(q for q in result["questions"] if q["facet"] == "duration")
    assert duration["options"] == []
    assert duration["options_from"] is None
    assert duration["free"] is True


def test_the_shape_question_asks_about_the_endpoints_own_size_parameter():
    aspect_of = lambda slug: next(q for q in plan("a kite over a field",
                                                  model_detail=model_detail(slug))["questions"]
                                  if q["facet"] == "aspect")
    sora = aspect_of("sora-2")
    assert sora["options"] == ["720x1280", "1280x720"]
    assert sora["options_from"]["parameter"] == "size"
    veo = aspect_of("veo3-fast")
    assert veo["options"] == ["16:9"]
    assert veo["options_from"]["parameter"] == "aspect_ratio"


# ------------------------------------------------------ lengths, read and totalled
def test_a_length_documented_with_a_unit_is_read_as_seconds():
    """"8s" is eight seconds, not an unreadable value: an endpoint that publishes
    its lengths with a unit was being treated as documenting none."""
    result = build_scenes("a kite over a field", model="veo3-fast",
                          model_detail=model_detail("veo3-fast"))
    assert result["scenes"][0]["seconds"] == "8s"
    assert result["totals"]["per_clip"] == "8s"
    assert result["totals"]["documented_lengths"] == ["8s"]
    assert result["totals"]["seconds"] == 8.0
    # The endpoint's own value is kept verbatim in the scene.
    assert result["scenes"][0]["params"].get("duration") == "8s"


def test_a_total_is_null_when_a_clip_length_is_unknown():
    """Summing the known clips and calling it the video's length is a claim the
    storyboard cannot support."""
    result = build_scenes("a kite over a field", model="veo3-fast",
                          model_detail=model_detail("veo3-fast"),
                          scene_seconds=4.0)
    # 4s snapped down to the documented 8s, so the length is known here...
    assert result["totals"]["seconds"] == 8.0
    unknown = build_scenes("a kite over a field", model="veo3-fast",
                           model_detail={"params": {}})
    assert unknown["totals"]["per_clip"] is None
    assert unknown["totals"]["seconds"] is None


def test_a_length_asked_for_in_prose_is_still_snapped_to_the_endpoints_own():
    result = build_scenes("a kite over a field for about 20 seconds", model="sora-2",
                          model_detail=model_detail("sora-2"))
    scene = result["scenes"][0]
    assert scene["seconds"] in ("4", "8", "12", "16", "20")
    assert scene["seconds"] in (result["totals"]["documented_lengths"] or [])
