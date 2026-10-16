"""Hugging Face's widget presets: the parse, the provenance, and the API.

The dataset is not hand-written — it is parsed from a vendored copy of the file the
Hub itself reads, so the first test here is a fidelity test: re-parse the vendored
source and require the committed JSON to be exactly that. If upstream changes shape,
that test fails rather than the tool quietly serving a stale or invented preset.

Run:  python -m pytest -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from promptlayer import (  # noqa: E402
    hf_model_presets,
    hf_presets,
    preset_index,
    preset_languages,
    preset_source,
    preset_tasks,
    widget_example_sections,
    widget_example_types,
    widget_examples,
)
from promptlayer import presets as presets_mod  # noqa: E402
from build_hf_presets import build  # noqa: E402
from build_hf_widget_examples import build as build_examples  # noqa: E402

COMMITTED = os.path.join(REPO, "promptlayer", "hf-presets.json")
VENDOR = os.path.join(REPO, "promptlayer", "vendor", "hf-default-widget-inputs.ts")
EXAMPLES_COMMITTED = os.path.join(REPO, "promptlayer", "hf-widget-examples.json")
EXAMPLES_VENDOR = os.path.join(REPO, "promptlayer", "vendor", "hf-models-widgets-examples.md")


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """Nothing in these tests may reach the network: the Hub read is stubbed to fail."""
    def offline(*args, **kwargs):
        raise urllib.error.URLError("tests are offline")

    monkeypatch.setattr(presets_mod, "_urlopen", offline)
    presets_mod.reset_model_cache()
    yield
    presets_mod.reset_model_cache()


# ---------------------------------------------------------------- fidelity
def test_the_committed_dataset_is_exactly_what_the_vendored_file_says():
    """Re-parse the vendored TS and compare — the dataset cannot drift from its source."""
    fresh = build()
    with open(COMMITTED, encoding="utf-8") as fh:
        committed = json.load(fh)
    assert committed["presets"] == fresh["presets"]
    assert committed["languages"] == fresh["languages"]
    assert committed["counts"] == fresh["counts"]


def test_the_vendored_file_is_the_one_we_think_it_is():
    assert os.path.exists(VENDOR)
    with open(VENDOR, encoding="utf-8") as fh:
        src = fh.read()
    assert "MAPPING_DEFAULT_WIDGET" in src
    assert "new Map<LanguageCode, PerLanguageMapping>" in src


def test_every_preset_is_traceable_to_a_fetchable_source():
    source = preset_source()
    for key in ("raw_url", "blob_url", "docs", "what", "repo", "path"):
        assert source.get(key), f"the dataset is missing source.{key}"
    # The honesty rule the rest of the app follows: a source must be fetchable,
    # i.e. a real URL, not a name.
    assert "/" in source["blob_url"] and source["blob_url"].startswith("https://")
    assert "/" in source["raw_url"] and source["raw_url"].startswith("https://")
    for entry in hf_presets():
        assert entry["source"] == source["blob_url"]
        assert entry["evidence"], "a preset must say which key it came from"
        assert entry["language"] and entry["task"]
        assert entry.get("text") is not None or entry.get("input") is not None


# ---------------------------------------------------------------- the collection
def test_the_collection_is_complete():
    index = preset_index()
    assert index["total"] == sum(index["languages"].values())
    assert index["total"] > 150, "the Hub ships far more defaults than this"
    assert len(index["languages"]) == 14
    assert "en" in index["languages"]
    assert preset_languages() == sorted(preset_languages())


def test_english_presets_are_the_documented_ones():
    """Spot-check against the published example inputs, so a parser bug cannot pass."""
    by_task = {}
    for preset in hf_presets(lang="en"):
        by_task.setdefault(preset["task"], preset)
    assert by_task["fill-mask"]["text"] == "Paris is the <mask> of France."
    assert by_task["question-answering"]["input"] == {
        "text": "Where do I live?", "context": "My name is Wolfgang and I live in Berlin"}
    assert by_task["text-classification"]["text"] == "I like you. I love you"
    assert by_task["sentence-similarity"]["input"]["sentences"] == [
        "That is a happy dog", "That is a very happy person", "Today is a sunny day"]
    # `[...].join("\n")` in the upstream file — the parser has to evaluate it, not quote it.
    joined = [p for p in hf_presets(lang="fa", task="summarization")]
    assert joined and "\n" in joined[0]["text"]


def test_the_runtime_defaults_cover_text_tasks_only():
    """The honest boundary: HF's site-wide defaults ship no image or video example.

    Vision and video examples exist on model cards and in the widget docs, which is
    why the second collection and the live card read exist at all.
    """
    tasks = set(preset_tasks())
    for vision_task in ("text-to-image", "image-to-video", "image-classification",
                        "visual-question-answering"):
        assert vision_task not in tasks
    assert {"text-generation", "fill-mask", "summarization"} <= tasks


# ------------------------------------------------- the documented widget examples
def test_the_documented_examples_are_exactly_what_the_vendored_doc_says():
    fresh = build_examples()
    with open(EXAMPLES_COMMITTED, encoding="utf-8") as fh:
        committed = json.load(fh)
    assert committed["types"] == fresh["types"]
    assert committed["counts"] == fresh["counts"]
    assert committed["sections"] == fresh["sections"]


def test_the_documented_examples_cover_every_widget_type_including_video():
    sections = widget_example_sections()
    assert sections == ["Natural Language Processing", "Audio", "Computer Vision", "Other"]
    types = widget_example_types()
    assert len(types) == 28
    assert {"Text-to-Image", "Image-to-Video", "Image Classification",
            "Visual Question Answering"} <= set(types)
    assert widget_example_types("Computer Vision") == [
        "Image Classification", "Object Detection", "Image Segmentation", "Image-to-Image",
        "Image-to-Video", "Text-to-Image", "Document Question Answering",
        "Visual Question Answering", "Zero-Shot Image Classification"]


def test_a_widget_entry_keeps_the_source_verbatim_and_flags_nested_fields():
    text_to_image = widget_examples(task="text-to-image")[0]
    assert text_to_image["source"].endswith("models-widgets-examples.md")
    assert text_to_image["evidence"] == "### Text-to-Image"
    assert text_to_image["yaml"] == (
        "widget:\n"
        '- text: "A cat playing with a ball"\n'
        '  example_title: "Cat"\n'
        '- text: "A dog jumping over a fence"\n'
        '  example_title: "Dog"')
    assert text_to_image["examples"] == [
        {"text": "A cat playing with a ball", "example_title": "Cat"},
        {"text": "A dog jumping over a fence", "example_title": "Dog"},
    ]

    # Sentence Similarity nests a list; the verbatim block keeps it, the parse flags it
    # rather than flattening it into something the doc does not say.
    similar = widget_examples(task="sentence-similarity")[0]
    assert similar["examples"][0]["nested"] is True
    assert "- \"That is a happy dog\"" in similar["yaml"]

    # An inline `#` comment must not become part of the value.
    video = widget_examples(task="image-to-video")[0]
    assert video["examples"][0]["prompt"] == "Girl with Pearl Earring"
    assert video["examples"][0]["src"].endswith("canny-edge.jpg")


def test_documented_examples_filter_and_never_invent():
    assert widget_examples(section="Audio") == widget_examples(section="Audio")
    assert len(widget_examples(section="Audio")) == 5
    assert widget_examples(task="telepathy") == []
    assert widget_examples(section="Telepathy") == []
    # Every entry in the flattened list cites the doc it came from.
    for entry in widget_examples():
        assert entry["source"].startswith("https://github.com/huggingface/hub-docs/blob/")
        assert entry["evidence"].startswith("### ")
        assert entry["yaml"].startswith("widget:")


def test_the_index_reports_both_collections():
    index = preset_index()
    assert index["collections"]["defaults"]["total"] == index["total"]
    assert index["collections"]["defaults"]["languages"] == 14
    assert index["collections"]["widget-examples"]["types"] == 28
    assert index["collections"]["widget-examples"]["examples"] > 40
    assert "docs/hub/models-widgets-examples.md" in index["collections"]["widget-examples"]["source"]


def test_filters_narrow_and_never_invent():
    assert len(hf_presets(lang="en")) == preset_index()["languages"]["en"]
    generation = hf_presets(lang="en", task="text-generation")
    assert generation and all(p["task"] == "text-generation" for p in generation)
    assert hf_presets(lang="en", task="text-to-video") == []
    assert hf_presets(lang="tlh") == []
    assert "text-generation" in preset_tasks()
    assert "text-generation" in preset_tasks("en")


def test_non_english_presets_are_kept_in_their_own_language():
    chinese = hf_presets(lang="zh")
    assert chinese and any(any("\u4e00" <= c <= "\u9fff" for c in p.get("text", ""))
                           for p in chinese)


# ---------------------------------------------------------------- the live card read
def test_a_model_card_is_read_over_http_and_reported(monkeypatch):
    payload = {
        "pipeline_tag": "zero-shot-image-classification",
        "downloads": 12,
        "cardData": {"widget": [{"src": "https://example.invalid/cat.jpg",
                                 "example_title": "Cat"},
                                {"src": "https://example.invalid/dog.jpg"}]},
    }

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(payload).encode()

    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url if hasattr(request, "full_url") else request
        return _Response()

    monkeypatch.setattr(presets_mod, "_urlopen", fake_urlopen)
    presets_mod.reset_model_cache()
    result = hf_model_presets("openai/clip-vit-base-patch32")
    assert result["widget"] == payload["cardData"]["widget"]
    assert result["pipeline_tag"] == "zero-shot-image-classification"
    assert result["live"] is True
    assert "huggingface.co/api/models/openai/clip-vit-base-patch32" in seen["url"]
    assert "error" not in result

    # Cached: the second read must not go out again.
    monkeypatch.setattr(presets_mod, "_urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("second fetch")))
    assert hf_model_presets("openai/clip-vit-base-patch32")["widget"] == result["widget"]


def test_a_card_without_widget_examples_says_so_instead_of_inventing(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"cardData": {}}).encode()

    monkeypatch.setattr(presets_mod, "_urlopen", lambda *a, **k: _Response())
    presets_mod.reset_model_cache()
    result = hf_model_presets("some/model")
    assert result["widget"] == []
    assert "no `widget:` examples" in result["error"]


def test_an_unreachable_hub_is_reported_not_raised(monkeypatch):
    def boom(*args, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(presets_mod, "_urlopen", boom)
    presets_mod.reset_model_cache()
    result = hf_model_presets("some/model")
    assert result["widget"] == []
    assert "could not reach huggingface.co" in result["error"]
    assert hf_model_presets("")["error"] == "model id is required"


def test_the_default_collection_needs_no_network_at_all():
    """The autouse fixture makes any urlopen an error, so this passing is the proof."""
    assert preset_index()["total"] > 150


# ---------------------------------------------------------------- the API
def _load_function(name: str):
    spec = importlib.util.spec_from_file_location(
        f"vf_{name}", os.path.join(REPO, "api", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler


@pytest.fixture(scope="module")
def presets_server():
    handler = _load_function("presets")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_api_returns_the_whole_collection_with_its_source(presets_server):
    status, body = _get(presets_server, "/")
    assert status == 200
    data = body["data"]
    assert data["total"] == preset_index()["total"]
    assert data["returned"] == data["total"] or data["returned"] == 400
    assert data["source"].endswith("default-widget-inputs.ts")
    assert data["presets"][0]["evidence"].startswith("MAPPING_")


def test_api_filters_and_rejects_unknown_values(presets_server):
    status, body = _get(presets_server, "/?lang=en&task=fill-mask")
    assert status == 200
    assert body["data"]["total"] == len(hf_presets(lang="en", task="fill-mask"))
    assert all(p["language"] == "en" for p in body["data"]["presets"])

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(presets_server, "/?lang=klingon")
    assert exc.value.code == 400
    assert "en" in json.loads(exc.value.read().decode())["available"]

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(presets_server, "/?task=telepathy")
    assert exc.value.code == 400


def test_api_limit_is_clamped_not_trusted(presets_server):
    status, body = _get(presets_server, "/?limit=3")
    assert status == 200 and len(body["data"]["presets"]) == 3
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(presets_server, "/?limit=lots")
    assert exc.value.code == 400


def test_api_model_read_never_happens_unless_asked(presets_server):
    """With the Hub unreachable the answer is the reason, and the collection is intact."""
    status, body = _get(presets_server, "/?model=some/model")
    assert status == 200
    assert body["data"]["widget"] == []
    assert "could not reach huggingface.co" in body["data"]["error"]
    assert body["index"]["total"] == preset_index()["total"]


def test_api_serves_the_documented_widget_examples(presets_server):
    status, body = _get(presets_server, "/?collection=widget-examples")
    assert status == 200
    data = body["data"]
    assert data["collection"] == "widget-examples"
    assert data["total"] == 28
    assert data["examples"] > 40
    assert data["source"].endswith("models-widgets-examples.md")

    status, body = _get(presets_server, "/?collection=widget-examples&section=Computer%20Vision")
    assert status == 200
    assert body["data"]["total"] == 9
    assert all(e["section"] == "Computer Vision" for e in body["data"]["presets"])
    # The type dropdown narrows with the section instead of offering all 28.
    assert body["data"]["types"] == [
        "Image Classification", "Object Detection", "Image Segmentation", "Image-to-Image",
        "Image-to-Video", "Text-to-Image", "Document Question Answering",
        "Visual Question Answering", "Zero-Shot Image Classification"]
    assert body["data"]["type_slugs"][4] == "image-to-video"

    status, body = _get(presets_server, "/?collection=widget-examples&task=image-to-video")
    assert status == 200
    assert body["data"]["total"] == 1
    assert body["data"]["presets"][0]["yaml"].startswith("widget:")

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(presets_server, "/?collection=telepathy")
    assert exc.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(presets_server, "/?collection=widget-examples&section=Telepathy")
    assert exc.value.code == 400
    assert "Computer Vision" in json.loads(exc.value.read().decode())["available"]


def test_the_shallow_parse_agrees_with_a_real_yaml_parser():
    """Dev-only cross-check: PyYAML is not a dependency, but where it exists it must agree."""
    yaml = pytest.importorskip("yaml")
    for entry in widget_examples():
        truth = yaml.safe_load(entry["yaml"])["widget"]
        assert len(truth) == len(entry["examples"]), entry["type"]
        for parsed, real in zip(entry["examples"], truth):
            for key, value in parsed.items():
                if key == "nested":
                    continue
                if isinstance(real.get(key), (list, dict)):
                    assert value is None, f"{entry['type']}.{key} was flattened"
                    continue
                assert value == real.get(key), f"{entry['type']}.{key}"
