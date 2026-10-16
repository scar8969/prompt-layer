"""Tests for reference images and design elements.

The rule: an image is measured in the browser and only the numbers travel. Nothing
here accepts pixel data, and nothing here invents a colour, a role or a rule.

Run:  python -m pytest -q
"""

from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from promptlayer import (  # noqa: E402
    MAX_IMAGES,
    apply_palette,
    build_reference_set,
    design_summary,
    load_design,
    model_detail,
    reference_roles,
    role_support,
)
from promptlayer.reference import clean_image  # noqa: E402


def _image(**over):
    base = {"name": "frame.png", "role": "first_frame", "width": 1280, "height": 720,
            "type": "image/png", "bytes": 240000, "palette": ["#0a1b2c", "#f0e0d0"]}
    base.update(over)
    return base


# ----------------------------------------------------------------- the roles
def test_roles_are_sourced_and_every_one_explains_itself():
    roles = reference_roles()
    for role in roles:
        assert role["source"] and role["evidence"], role["id"]
        assert role["label"] and role["why"], role["id"]


def test_the_first_frame_is_supported_everywhere_an_image_is():
    for slug in ("hailuo-02-i2v", "wan-2.2-a14b-i2v", "sora-2"):
        support = role_support("first_frame", model_detail(slug))
        assert support["supported"] is True, slug
        assert support["how"], slug


def test_a_role_the_endpoint_cannot_use_is_reported_not_dropped():
    """sora-2 documents one input reference, not a last frame."""
    support = role_support("last_frame", model_detail("sora-2"))
    assert support["supported"] is False
    assert "documents" in support["reason"]
    assert support["source"] and support["evidence"]

    support = role_support("last_frame", model_detail("hailuo-02-i2v"))
    assert support["supported"] is True
    assert "last_frame" in support["how"]


def test_an_endpoint_with_no_image_input_says_so():
    support = role_support("first_frame", model_detail("gpt-4o-mini"))
    assert support["supported"] is False
    assert "no image input" in support["reason"]


def test_a_reusable_character_role_maps_to_the_documented_parameter():
    support = role_support("subject", model_detail("sora-2"))
    assert support["supported"] is True
    assert "characters" in support["how"]


# ----------------------------------------------------------------- the numbers
def test_a_colour_is_normalised_and_a_bad_one_is_dropped():
    assert clean_image(_image(palette=["0a1b2c", "#ABC", "not-a-colour"]), 1)["palette"] == [
        "#0a1b2c", "#aabbcc"]


def test_an_image_without_measurements_is_not_accepted():
    assert clean_image({"width": 0, "height": 0, "bytes": 0}, 1)["width"] == 0
    assert clean_image({"width": 99999, "height": 10, "bytes": 5}, 1) is None
    assert clean_image("not an object", 1) is None
    assert clean_image({"width": "abc", "height": 10}, 1) is None


def test_only_the_numbers_travel_and_nothing_else():
    """Whatever else the browser sends, the output is measurements and nothing more."""
    result = build_reference_set([_image(data="AAAA", pixels=[1, 2, 3])], model_detail("sora-2"))
    image = result["images"][0]
    assert set(image) == {"index", "role", "role_asked", "name", "width", "height",
                          "type", "bytes", "palette", "scene"}


def test_the_set_is_capped():
    result = build_reference_set([_image() for _ in range(9)], model_detail("sora-2"))
    assert len(result["images"]) == MAX_IMAGES


def test_a_role_outside_the_catalog_falls_back_to_the_first_frame_and_says_so():
    result = build_reference_set([_image(role="hero_shot")], model_detail("sora-2"))
    image = result["images"][0]
    assert image["role"] == "first_frame"
    assert image["role_asked"] == "hero_shot"


# ----------------------------------------------------------------- the checks
def test_a_size_the_endpoint_does_not_document_is_flagged():
    result = build_reference_set([_image(width=1920, height=1080)], model_detail("sora-2"))
    check = next(c for c in result["checks"] if c["id"] == "reference_size")
    assert "1920x1080" in check["detail"]
    assert "720x1280" in check["detail"]
    assert check["source"] and check["evidence"]


def test_a_resolution_that_is_only_a_label_is_not_guessed_at():
    """Hailuo documents "768P", not pixel dimensions, so no size check is made."""
    result = build_reference_set([_image(width=1920, height=1080)], model_detail("hailuo-02-i2v"))
    assert not any(c["id"] == "reference_size" for c in result["checks"])


def test_a_documented_size_is_not_flagged():
    result = build_reference_set([_image(width=720, height=1280)], model_detail("sora-2"))
    assert not any(c["id"] == "reference_size" for c in result["checks"])


def test_a_type_outside_the_documented_set_is_flagged():
    result = build_reference_set([_image(type="image/tiff")], model_detail("sora-2"))
    assert any(c["id"] == "reference_type" for c in result["checks"])


def test_a_last_frame_on_an_endpoint_without_one_is_flagged():
    result = build_reference_set([_image(role="last_frame")], model_detail("sora-2"))
    check = next(c for c in result["checks"] if c["id"] == "role_not_supported")
    assert "last_frame" in check["detail"]
    assert check["source"] and check["evidence"]


def test_too_many_reusable_characters_is_flagged():
    images = [_image(role="subject", name=f"c{i}.png") for i in range(3)]
    result = build_reference_set(images, model_detail("sora-2"))
    assert any(c["id"] == "too_many_characters" for c in result["checks"])


# ----------------------------------------------------------------- the palette
def test_the_palette_line_uses_the_documented_shape():
    result = build_reference_set([_image()], model_detail("sora-2"))
    line = result["palette_line"]
    assert line["line"] == "Palette anchors: #0a1b2c, #f0e0d0"
    assert line["source"].endswith("sora2_prompting_guide")
    assert "three to five" in line["evidence"]


def test_the_palette_is_capped_and_ordered_by_the_images():
    images = [_image(name=f"{i}.png", palette=["#111111", "#222222", "#333333"]) for i in range(3)]
    result = build_reference_set(images, model_detail("sora-2"))
    assert result["palette"] == ["#111111", "#222222", "#333333"]
    assert len(result["palette_line"]["colours"]) <= 5


def test_no_images_means_no_palette_line():
    result = build_reference_set([], model_detail("sora-2"))
    assert result["palette_line"] is None
    assert result["palette"] == []


def test_appending_a_palette_keeps_the_sentence_well_formed():
    line = {"line": "Palette anchors: #0a1b2c"}
    assert apply_palette("a fox crosses a field", line) == \
        "a fox crosses a field. Palette anchors: #0a1b2c."
    assert apply_palette("a fox runs.", line) == "a fox runs. Palette anchors: #0a1b2c."
    assert apply_palette("a fox runs", None) == "a fox runs"


# ----------------------------------------------------------------- the sheet
def test_the_reference_sheet_quotes_the_motion_only_rule():
    result = build_reference_set([_image()], model_detail("hailuo-02-i2v"))
    sheet = result["sheet"]
    assert "Send it as: first_frame_image" in sheet["lines"][0]
    assert any("never re-describe" in line for line in sheet["lines"])
    assert sheet["source"] and sheet["evidence"]


def test_the_rules_come_from_the_design_dataset_not_from_this_module():
    design = load_design()["image_reference"]
    result = build_reference_set([_image()], model_detail("sora-2"))
    for rule in design["rules"]:
        assert rule in result["rules"]


# ----------------------------------------------------------------- the design set
def test_a_known_element_reports_the_source_it_came_from():
    summary = design_summary({"light_source": "moonlight"}, None)
    assert summary[0]["known"] is True
    assert summary[0]["source"] and summary[0]["evidence"]


def test_an_unknown_element_is_marked_unknown():
    summary = design_summary({"tone": "vibes"}, None)
    assert summary[0]["known"] is False
    assert summary[0]["source"] is None


def test_the_palette_is_listed_as_an_element_with_its_source():
    line = {"colours": ["#0a1b2c"], "source": "sora doc — example.com/x",
            "evidence": "Palette anchors: ..."}
    summary = design_summary({}, line)
    assert summary[0]["element"] == "palette"
    assert summary[0]["known"] is True
    assert summary[0]["source"] == "sora doc — example.com/x"
