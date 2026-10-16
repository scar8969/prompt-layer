"""Hugging Face's widget presets, and the presets a specific model page shows.

Two collections, both from huggingface.co:

1. **Default widget inputs** — the example prompts the Hub shows in a model
   widget's examples dropdown when the model card defines none. They live in
   `huggingface/huggingface.js -> packages/tasks/src/default-widget-inputs.ts`
   (`MAPPING_DEFAULT_WIDGET`), vendored under `promptlayer/vendor/` and parsed into
   `promptlayer/hf-presets.json` by `scripts/build_hf_presets.py`. 14 languages.
2. **Model-card presets** — the `widget:` metadata of one model, which is what its
   own page shows. Read live from the Hub API on request; nothing is fetched unless
   you ask for a model.

Both are read-only public data. The loader never invents an entry: a missing
language, task or model returns empty with a reason.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS_PATH = os.path.join(HERE, "hf-presets.json")
VENDOR_PATH = os.path.join(HERE, "vendor", "hf-default-widget-inputs.ts")
EXAMPLES_PATH = os.path.join(HERE, "hf-widget-examples.json")
EXAMPLES_VENDOR_PATH = os.path.join(HERE, "vendor", "hf-models-widgets-examples.md")
HF_MODEL_API = "https://huggingface.co/api/models/"

# Indirection so tests can stub the single outbound call without patching the
# global urllib.request.urlopen (which every other HTTP client in the process uses).
_urlopen = urllib.request.urlopen

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_examples_cache: Optional[Dict[str, Any]] = None
_model_cache: Dict[str, Dict[str, Any]] = {}


def load_presets(path: str = PRESETS_PATH) -> Dict[str, Any]:
    """The parsed dataset, read once per process."""
    global _cache
    with _lock:
        if _cache is None:
            with open(path, encoding="utf-8") as fh:
                _cache = json.load(fh)
        return _cache


def reload_presets() -> Dict[str, Any]:
    global _cache
    with _lock:
        _cache = None
    return load_presets()


def preset_source() -> Dict[str, Any]:
    return dict(load_presets()["source"])


def preset_languages() -> List[str]:
    return list(load_presets()["languages"])


def preset_tasks(lang: Optional[str] = None) -> List[str]:
    """Task names, across all languages or for one language."""
    presets = load_presets()["presets"]
    if lang:
        return sorted(presets.get(lang, {}))
    tasks = set()
    for tasks_for_lang in presets.values():
        tasks.update(tasks_for_lang)
    return sorted(tasks)


def hf_presets(lang: Optional[str] = None, task: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flatten the dataset to a list, optionally filtered.

    Each entry carries where it came from, so a preset shown in the UI can always
    be traced to the upstream file and the language/task key it lives under.
    """
    data = load_presets()
    source = data["source"]
    languages = [lang] if lang else data["languages"]
    out: List[Dict[str, Any]] = []
    for code in languages:
        tasks = data["presets"].get(code, {})
        for name in ([task] if task else sorted(tasks)):
            for index, item in enumerate(tasks.get(name, [])):
                entry = {
                    "language": code,
                    "task": name,
                    "index": index,
                    "source": source["blob_url"],
                    "evidence": f'MAPPING_{code.upper()} -> "{name}" [{index}]',
                }
                if isinstance(item, str):
                    entry["text"] = item
                else:
                    entry["input"] = item
                out.append(entry)
    return out


def preset_index() -> Dict[str, Any]:
    """Counts and provenance for both collections, for the model list and the UI."""
    data = load_presets()
    examples = load_widget_examples()
    return {
        "total": data["counts"]["total"],
        "languages": data["counts"]["by_language"],
        "tasks": preset_tasks(),
        "source": data["source"]["blob_url"],
        "version": data["version"],
        "collections": {
            "defaults": {
                "label": "Hub defaults (text tasks)",
                "total": data["counts"]["total"],
                "languages": len(data["languages"]),
                "tasks": len(preset_tasks()),
                "source": data["source"]["blob_url"],
                "version": data["version"],
            },
            "widget-examples": {
                "label": "Documented widget examples (all tasks, incl. vision and video)",
                "types": examples["counts"]["types"],
                "examples": examples["counts"]["examples"],
                "sections": examples["counts"]["by_section"],
                "source": examples["source"]["blob_url"],
                "version": examples["version"],
            },
        },
    }


# ---------------------------------------------- documented widget examples (docs)

def load_widget_examples(path: str = EXAMPLES_PATH) -> Dict[str, Any]:
    global _examples_cache
    with _lock:
        if _examples_cache is None:
            with open(path, encoding="utf-8") as fh:
                _examples_cache = json.load(fh)
        return _examples_cache


def reload_widget_examples() -> Dict[str, Any]:
    global _examples_cache
    with _lock:
        _examples_cache = None
    return load_widget_examples()


def widget_example_types(section: Optional[str] = None) -> List[str]:
    types = [t["type"] for t in load_widget_examples()["types"]]
    if section:
        return [t["type"] for t in load_widget_examples()["types"] if t["section"] == section]
    return types


def widget_example_sections() -> List[str]:
    return list(load_widget_examples()["sections"])


def widget_examples(task: Optional[str] = None,
                    section: Optional[str] = None) -> List[Dict[str, Any]]:
    """The documented example inputs, one entry per widget type (filtered).

    `task` accepts either the widget type as written in the doc ("Text-to-Image") or
    its slug ("text-to-image"), because the UI labels options with the former and
    sends the latter. Each entry keeps the upstream YAML verbatim plus the shallow
    parse, so nothing shown in the UI is a re-telling of the source.
    """
    data = load_widget_examples()
    source = data["source"]
    out = []
    for entry in data["types"]:
        if task and task not in (entry["task"], entry["type"]):
            continue
        if section and entry["section"] != section:
            continue
        out.append({
            "type": entry["type"],
            "task": entry["task"],
            "section": entry["section"],
            "examples": entry["examples"],
            "yaml": entry["yaml"],
            "source": source["blob_url"],
            "evidence": f"### {entry['type']}",
        })
    return out


def widget_example_slugs() -> List[str]:
    """The query values the widget types answer to."""
    return [entry["task"] for entry in load_widget_examples()["types"]]


# ---------------------------------------------------------------- live model cards

def hf_model_presets(model_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    """The presets one model's page shows, from its card's `widget:` metadata.

    This is the only outbound call in the app that is not behind the compile gate,
    and it is deliberate: it reads one public JSON document for a model the user
    named, sends nothing, and never runs unless asked for. Failure is reported, not
    raised — an offline machine still gets the default collection.
    """
    if not isinstance(model_id, str) or not model_id.strip():
        return {"model": "", "error": "model id is required", "widget": []}
    model_id = model_id.strip()
    if model_id in _model_cache:
        return _model_cache[model_id]

    url = HF_MODEL_API + urllib.parse.quote(model_id)
    result: Dict[str, Any] = {"model": model_id, "url": url, "live": True, "widget": []}
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "prompt-layer/1.0", "Accept": "application/json"})
        with _urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        result["error"] = f"the Hub returned {exc.code} for that model id"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result["error"] = f"could not reach huggingface.co ({type(exc).__name__})"
    except json.JSONDecodeError:
        result["error"] = "the Hub returned something that is not JSON"
    else:
        card = payload.get("cardData") or {}
        widget = card.get("widget") or []
        result["widget"] = widget if isinstance(widget, list) else []
        result["pipeline_tag"] = payload.get("pipeline_tag")
        result["downloads"] = payload.get("downloads")
        result["source"] = url
        if not result["widget"]:
            result["error"] = ("this model card defines no `widget:` examples, so its page shows "
                               "the default presets above")
    _model_cache[model_id] = result
    return result


def reset_model_cache() -> None:
    _model_cache.clear()
