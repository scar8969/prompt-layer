#!/usr/bin/env python3
"""Derive the image endpoints' `params` blocks from their own vendored schemas.

The six image endpoints are the ones whose published schema was fetched and kept
under `promptlayer/vendor/fal/`. Their `params` block is therefore not typed in by
hand: it is derived here, from the vendored schema, and
`tests/test_fal_dataset.py` re-derives it on every run and fails if the committed
dataset has drifted — the same fidelity rule the Hugging Face presets follow.

`prompt` is the one property left out: it is the input, not a parameter the
endpoint fills in. Everything else the schema publishes is carried, so the claim
"every parameter, with its source" is checkable rather than aspirational.

    python scripts/build_fal_params.py --check    # exit 1 if the dataset drifted
    python scripts/build_fal_params.py --write    # write the derived blocks back
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENDOR = os.path.join(REPO, "promptlayer", "vendor", "fal")
LAYERS = os.path.join(REPO, "promptlayer", "layers.json")

# slug -> the vendored schema file its endpoint publishes.
ENDPOINTS = {
    "flux-2": "fal-ai_flux-2.json",
    "flux-2-pro": "fal-ai_flux-2-pro.json",
    "flux-2-edit": "fal-ai_flux-2_edit.json",
    "seedream-v4": "fal-ai_bytedance_seedream_v4_text-to-image.json",
    "qwen-image": "fal-ai_qwen-image.json",
    "nano-banana": "fal-ai_nano-banana.json",
}

# The input itself, not a parameter the endpoint fills in.
SKIP = {"prompt"}


def load_document(slug: str) -> dict:
    path = os.path.join(VENDOR, ENDPOINTS[slug])
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def input_schema(document: dict) -> dict:
    """The schema the POST body is validated against (`*Input`)."""
    schemas = document.get("components", {}).get("schemas", {})
    inputs = [v for k, v in schemas.items() if k.lower().endswith("input")]
    if len(inputs) != 1:
        raise ValueError(f"expected exactly one *Input schema, found {len(inputs)}")
    return inputs[0]


def endpoint_id(document: dict) -> str:
    """The endpoint path the schema is mounted at, e.g. 'fal-ai/flux-2'.

    The POST that carries a request body is the endpoint itself; the other paths
    are its queue-status and cancel routes, and the placeholder paths belong to
    individual requests.
    """
    for path, operations in document.get("paths", {}).items():
        if "{" in path:
            continue
        post = operations.get("post")
        if isinstance(post, dict) and "requestBody" in post:
            return path.lstrip("/")
    raise ValueError("no endpoint path in the document")


def build(slug: str) -> dict:
    """The params block this endpoint's own schema publishes, in published order."""
    schema = input_schema(load_document(slug))
    properties = schema.get("properties", {})
    params = {}
    for key, spec in properties.items():
        if key in SKIP:
            continue
        entry = {"default": spec.get("default", None)}
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            entry["options"] = [str(value) for value in enum]
        params[key] = entry
    return params


def _scalar(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    # Match the file's own style for a nested object: { "width": 2048, ... }.
    if isinstance(value, dict) and text != "{}":
        text = "{ " + text[1:-1] + " }"
    return text


def render(params: dict, indent: str = "        ") -> str:
    """The params block in the style layers.json is written in."""
    lines = ["{"]
    entries = []
    for key, entry in params.items():
        parts = [f'"default": {_scalar(entry["default"])}']
        if entry.get("options"):
            parts.append('"options": [' + ", ".join(_scalar(o) for o in entry["options"]) + "]")
        entries.append(f'{indent}  "{key}": {{ ' + ", ".join(parts) + " }")
    lines.append(",\n".join(entries))
    lines.append(indent + "}")
    return "\n".join(lines)


def committed() -> dict:
    with open(LAYERS, encoding="utf-8") as fh:
        return json.load(fh)


def drift() -> list:
    """Every difference between the committed params and the derived ones."""
    data = committed()
    out = []
    for slug in ENDPOINTS:
        model = data["models"].get(slug)
        if model is None:
            out.append(f"{slug}: not in layers.json")
            continue
        have, want = model.get("params", {}), build(slug)
        for key in sorted(set(have) | set(want)):
            if key not in want:
                out.append(f"{slug}.{key}: documented but not in the endpoint's schema")
            elif key not in have:
                out.append(f"{slug}.{key}: in the schema (default {want[key]['default']!r}) but not documented")
            elif have[key] != want[key]:
                out.append(f"{slug}.{key}: {have[key]} != schema {want[key]}")
    return out


def write() -> int:
    """Replace each model's params block in place, leaving the rest of the file alone."""
    raw = open(LAYERS, encoding="utf-8").read()
    changed = 0
    for slug in ENDPOINTS:
        start = raw.find(f'"{slug}": {{')
        if start < 0:
            print(f"  {slug}: not found, skipped")
            continue
        key = raw.find('"params": {', start)
        if key < 0:
            print(f"  {slug}: no params block, skipped")
            continue
        # Walk braces so the block ends where the object does, not at the first '}'.
        depth, pos = 0, raw.find("{", key)
        end = pos
        while pos < len(raw):
            if raw[pos] == "{":
                depth += 1
            elif raw[pos] == "}":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    break
            pos += 1
        block = render(build(slug), indent="      ")
        new = raw[:key] + '"params": ' + block + raw[end:]
        if new != raw:
            changed += 1
        raw = new
    with open(LAYERS, "w", encoding="utf-8", newline="") as fh:
        fh.write(raw)
    return changed


if __name__ == "__main__":
    if "--write" in sys.argv:
        n = write()
        print(f"rewrote the params of {n} model(s) from the vendored schemas")
    else:
        differences = drift()
        if differences:
            print(f"{len(differences)} difference(s) from the vendored schemas:")
            for line in differences:
                print("  -", line)
            sys.exit(1)
        print(f"{len(ENDPOINTS)} image endpoints match their vendored schemas exactly")
