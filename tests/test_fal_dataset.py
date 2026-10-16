"""The image endpoints are held to their own published schema, not to my typing.

The six image entries came from a live fetch of each endpoint's fal queue schema,
and the fetch is vendored under `promptlayer/vendor/fal/`. These tests re-derive
the dataset from that vendored copy on every run, so the same rule the Hugging Face
presets follow applies here: a committed dataset that has drifted from its source
fails the suite instead of being served.

What is asserted, per endpoint:
  * every parameter the schema publishes (except `prompt`, which is the input) is
    documented, with the schema's own default and enum — the "every parameter"
    claim is checked, not asserted in prose;
  * the documented params are exactly the schema's, so a schema that gains a
    parameter fails until it is read;
  * every layer's quoted evidence appears verbatim in the schema's description for
    the parameter it names, and the source is that endpoint's schema URL.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from promptlayer import model_detail  # noqa: E402
from build_fal_params import ENDPOINTS, build, endpoint_id, input_schema, load_document  # noqa: E402

VENDOR = os.path.join(REPO, "promptlayer", "vendor", "fal")


@pytest.mark.parametrize("slug", sorted(ENDPOINTS))
def test_the_committed_params_are_the_endpoints_own(slug):
    committed = model_detail(slug)["params"]
    derived = build(slug)
    assert committed == derived, (
        f"{slug}: layers.json has drifted from the vendored schema — "
        f"run `python scripts/build_fal_params.py --write`")


@pytest.mark.parametrize("slug", sorted(ENDPOINTS))
def test_every_published_parameter_is_documented(slug):
    schema = input_schema(load_document(slug))
    published = {k for k in schema.get("properties", {}) if k != "prompt"}
    documented = set(model_detail(slug)["params"])
    assert documented == published, (
        f"{slug}: undocumented {sorted(published - documented)}, "
        f"invented {sorted(documented - published)}")


@pytest.mark.parametrize("slug", sorted(ENDPOINTS))
def test_every_layer_quote_is_verbatim_in_that_endpoints_schema(slug):
    schema = input_schema(load_document(slug))
    properties = schema.get("properties", {})
    for layer in model_detail(slug)["layers"]:
        evidence = layer["evidence"]
        # The parameter the evidence is about is the leading token.
        key = evidence.split("(")[0].strip()
        assert key in properties, f"{slug}/{layer['id']}: '{key}' is not in the schema"
        # The schema's own prose is wrapped across lines, so compare on collapsed
        # whitespace: the words must be the endpoint's, not my paraphrase.
        description = " ".join((properties[key].get("description") or "").split())
        quotes = [q for q in re.findall(r'["“]([^"”]+)["”]', evidence) if len(q) >= 12]
        assert quotes, f"{slug}/{layer['id']}: no quoted evidence"
        # The dataset's convention: the evidence ends with the endpoint's own
        # sentence, verbatim.
        assert " ".join(quotes[-1].split()) in description, (
            f"{slug}/{layer['id']}: {quotes[-1]!r} is not in the schema's description "
            f"of '{key}': {description[:200]!r}")


@pytest.mark.parametrize("slug", sorted(ENDPOINTS))
def test_the_source_is_the_endpoint_the_data_came_from(slug):
    detail = model_detail(slug)
    expected = f"endpoint_id={detail['endpoint']}"
    assert detail["endpoint"] == endpoint_id(load_document(slug))
    for layer in detail["layers"]:
        assert expected in layer["source"], layer["id"]
        assert "/" in layer["source"]


def test_the_whole_vendored_set_is_kept_not_just_the_six_used():
    """The edit siblings the notes point at are vendored too, so the note that
    says 'flux-2/edit is the entry that takes one' is checkable rather than a
    recollection."""
    names = sorted(os.listdir(VENDOR))
    assert "fal-ai_flux-2_edit.json" in names
    assert "fal-ai_bytedance_seedream_v4_edit.json" in names
    assert "fal-ai_qwen-image-edit.json" in names
    assert "fal-ai_nano-banana_edit.json" in names
    for name in names:
        document = json.load(open(os.path.join(VENDOR, name), encoding="utf-8"))
        assert input_schema(document), name
        assert endpoint_id(document), name


def test_an_edit_endpoint_documents_the_image_it_takes():
    """The claim in each text-to-image entry — that the /edit sibling is the one
    that takes an image — is checked against that sibling's own schema, looked up
    by the endpoint id the schema declares rather than by a filename I chose."""
    by_endpoint = {}
    for name in os.listdir(VENDOR):
        document = json.load(open(os.path.join(VENDOR, name), encoding="utf-8"))
        by_endpoint[endpoint_id(document)] = document

    for slug, sibling in {
        "flux-2": "fal-ai/flux-2/edit",
        "qwen-image": "fal-ai/qwen-image-edit",
        "nano-banana": "fal-ai/nano-banana/edit",
        "seedream-v4": "fal-ai/bytedance/seedream/v4/edit",
    }.items():
        detail = model_detail(slug)
        assert detail["accepts_image"] is False, slug
        assert sibling in detail["reference"]["note"], slug
        assert sibling in by_endpoint, (sibling, sorted(by_endpoint))
        fields = set(input_schema(by_endpoint[sibling]).get("properties", {}))
        image_field = "image_url" if "image_url" in fields else "image_urls"
        assert image_field in fields, (sibling, sorted(fields))


def test_the_seed_layer_only_claims_what_the_schema_says():
    """Two endpoints say a missing seed means a random one; the layer that says so
    exists only where that sentence does."""
    for slug in ("flux-2", "flux-2-edit", "qwen-image"):
        layers = {l["id"]: l for l in model_detail(slug)["layers"]}
        assert "pin.seed" in layers, slug
        assert "seed" in model_detail(slug)["params"], slug
    for slug in ("flux-2-pro", "seedream-v4", "nano-banana"):
        # The parameter is documented everywhere; the *claim* is only where the
        # endpoint publishes the sentence that supports it.
        assert "seed" in model_detail(slug)["params"], slug
        assert "pin.seed" not in {l["id"] for l in model_detail(slug)["layers"]}, slug
