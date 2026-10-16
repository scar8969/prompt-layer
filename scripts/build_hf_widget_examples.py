"""Regenerate promptlayer/hf-widget-examples.json from the vendored Hugging Face doc.

    python scripts/build_hf_widget_examples.py

The dataset is the documented example inputs for every Hub widget type:

    huggingface/hub-docs -> docs/hub/models-widgets-examples.md

This is the half of Hugging Face's presets that `default-widget-inputs.ts` does not
cover: the doc is where the computer-vision and video widget types (Text-to-Image,
Image-to-Video, Image Classification, ...) are given example inputs. The runtime
defaults are text-only, so without this the tool would have nothing to offer an
image or video model.

Two levels are kept per widget type:

* `yaml`     — the code block verbatim, so the source of truth is never paraphrased
* `examples` — a shallow parse of the top-level list items (scalar fields only)

An item whose fields are nested (`sentences:`, `structured_data:`) is flagged
`nested: true` rather than flattened into something it is not. The verbatim block
still carries those values.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENDOR = os.path.join(REPO, "promptlayer", "vendor", "hf-models-widgets-examples.md")
OUT = os.path.join(REPO, "promptlayer", "hf-widget-examples.json")

UPSTREAM = {
    "repo": "huggingface/hub-docs",
    "path": "docs/hub/models-widgets-examples.md",
    "raw_url": ("https://raw.githubusercontent.com/huggingface/hub-docs/main/"
                "docs/hub/models-widgets-examples.md"),
    "blob_url": ("https://github.com/huggingface/hub-docs/blob/main/"
                 "docs/hub/models-widgets-examples.md"),
    "docs": "huggingface.co/docs/hub/models-widgets-examples",
    "what": ("The example inputs Hugging Face documents for each widget type — what a "
             "model card's `widget:` block renders on the model page. Covers the vision "
             "and video types the runtime defaults leave out."),
}

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_comment(value: str) -> str:
    """Drop a trailing `# ...` that is not inside quotes."""
    quote = None
    for index, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return value[:index].rstrip()
    return value.rstrip()


def _scalar(text: str):
    """A YAML scalar as Python, for the shapes this doc uses."""
    text = _unquote(_strip_comment(text))
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def parse_block(block: str):
    """The top-level `- key: value` items of a `widget:` block, shallow.

    `nested` is set only when a field really is nested, so a plain example stays a
    plain example instead of carrying a flag that means nothing.
    """
    examples, current, nested, key = [], None, False, None

    def close(item, is_nested):
        if is_nested:
            item["nested"] = True
        examples.append(item)

    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("widget:"):
            continue
        if line.startswith("- "):
            if current is not None:
                close(current, nested)
            current, nested, key = {}, False, None
            rest = line[2:]
        else:
            rest = line.strip()
        if current is None:
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", rest)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if value == "":
            # A nested mapping or list follows; keep the key visible, do not invent it.
            nested = True
            current[key] = None
            continue
        current[key] = _scalar(value)
    if current is not None:
        close(current, nested)
    return examples


def parse(markdown: str):
    """{section: [ {type, task, examples, yaml} ]} in document order."""
    sections, current_section = [], None
    heads = list(_HEADING.finditer(markdown))
    for index, head in enumerate(heads):
        level, title = head.group(1), head.group(2).strip()
        body_end = heads[index + 1].start() if index + 1 < len(heads) else len(markdown)
        body = markdown[head.end():body_end]
        if level == "##":
            current_section = title
            continue
        fence = _FENCE.search(body)
        if not fence:
            continue
        block = fence.group(1)
        sections.append({
            "type": title,
            "task": title.lower().replace(" ", "-").replace("_", "-"),
            "section": current_section,
            "examples": parse_block(block),
            "yaml": block.rstrip("\n"),
        })
    return sections


def build(vendor_path: str = VENDOR) -> dict:
    with open(vendor_path, encoding="utf-8") as fh:
        markdown = fh.read()
    types = parse(markdown)
    by_section: dict = {}
    for entry in types:
        by_section.setdefault(entry["section"], []).append(entry["type"])
    return {
        "version": "2026-10-16",
        "source": UPSTREAM,
        "counts": {
            "types": len(types),
            "examples": sum(len(t["examples"]) for t in types),
            "by_section": {k: len(v) for k, v in by_section.items()},
        },
        "sections": by_section,
        "types": types,
    }


def cross_check(types) -> None:
    """If PyYAML is importable, prove the shallow parse matches it. Dev-only."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("  (PyYAML not installed — skipping the cross-check)")
        return
    import yaml

    for entry in types:
        truth = yaml.safe_load(entry["yaml"])
        items = truth.get("widget") if isinstance(truth, dict) else None
        if not isinstance(items, list):
            continue
        assert len(items) == len(entry["examples"]), f"{entry['type']}: item count"
        for parsed, real in zip(entry["examples"], items):
            for key, value in parsed.items():
                if key == "nested":
                    continue
                if isinstance(real.get(key), (list, dict)):
                    assert value is None, f"{entry['type']}.{key}: nested value flattened"
                    continue
                assert value == real.get(key), f"{entry['type']}.{key}: {value!r} != {real.get(key)!r}"
    print("  cross-check against PyYAML: OK")


def main() -> int:
    data = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {OUT}")
    print(f"  {data['counts']['types']} widget types, {data['counts']['examples']} examples")
    for section, names in data["sections"].items():
        print(f"  {section}: {len(names)}")
    cross_check(data["types"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
