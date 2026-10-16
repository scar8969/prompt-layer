"""GET /api/presets — the presets Hugging Face's own site uses.

  GET /api/presets                                 Hub defaults (14 languages, 201)
  GET /api/presets?lang=en&task=text-generation    one language / one task
  GET /api/presets?collection=widget-examples      documented examples, all 28 types
  GET /api/presets?collection=widget-examples&section=Computer%20Vision
  GET /api/presets?model=<hf model id>             the presets that model's page shows

Only `model` touches the network, and only when it is asked for.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import (  # noqa: E402
    hf_model_presets,
    hf_presets,
    preset_index,
    preset_languages,
    preset_tasks,
    widget_example_sections,
    widget_example_slugs,
    widget_example_types,
    widget_examples,
)
from promptlayer.http import send_error_json, send_json  # noqa: E402

MAX_LIMIT = 400
COLLECTIONS = ("defaults", "widget-examples")


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)

        def one(name: str):
            values = query.get(name) or []
            return values[0] if values else None

        limit_raw = one("limit")
        limit = MAX_LIMIT
        if limit_raw:
            try:
                limit = max(1, min(MAX_LIMIT, int(limit_raw)))
            except ValueError:
                return send_error_json(self, 400, f"'limit' must be an integer <= {MAX_LIMIT}")

        model = one("model")
        if model:
            return send_json(self, 200, {
                "success": True,
                "data": hf_model_presets(model),
                "index": preset_index(),
            })

        collection = one("collection") or "defaults"
        if collection not in COLLECTIONS:
            return send_error_json(self, 400, f"Unknown collection '{collection}'",
                                   available=list(COLLECTIONS))

        if collection == "widget-examples":
            return self._widget_examples(one, limit)

        lang, task = one("lang"), one("task")
        if lang and lang not in preset_languages():
            return send_error_json(self, 400, f"Unknown language '{lang}'",
                                   available=preset_languages())
        if task and task not in preset_tasks():
            return send_error_json(self, 400, f"Unknown task '{task}'",
                                   available=preset_tasks())

        index = preset_index()
        presets = hf_presets(lang=lang, task=task)
        send_json(self, 200, {
            "success": True,
            "data": {
                "collection": "defaults",
                "source": index["source"],
                "version": index["version"],
                "languages": preset_languages(),
                "tasks": preset_tasks(),
                "counts": index["languages"],
                "filtered": {"language": lang, "task": task},
                "returned": min(len(presets), limit),
                "total": len(presets),
                "presets": presets[:limit],
            },
            "index": index,
        })

    def _widget_examples(self, one, limit: int) -> None:
        section, task = one("section"), one("task")
        if section and section not in widget_example_sections():
            return send_error_json(self, 400, f"Unknown section '{section}'",
                                   available=widget_example_sections())
        if task and task not in widget_example_types() and task not in widget_example_slugs():
            return send_error_json(self, 400, f"Unknown widget type '{task}'",
                                   available=widget_example_slugs())

        entries = widget_examples(task=task, section=section)
        index = preset_index()
        examples = index["collections"]["widget-examples"]
        # The type list follows the section filter, so the UI's dropdown offers only
        # the types that can actually return something for the current section.
        filtered_types = widget_example_types(section) if section else widget_example_types()
        send_json(self, 200, {
            "success": True,
            "data": {
                "collection": "widget-examples",
                "source": examples["source"],
                "version": examples["version"],
                "sections": widget_example_sections(),
                "types": filtered_types,
                "type_slugs": [entry["task"] for entry in widget_examples(section=section)]
                               if section else widget_example_slugs(),
                "counts": examples["sections"],
                "filtered": {"section": section, "task": task},
                "returned": min(len(entries), limit),
                "total": len(entries),
                "examples": sum(len(entry["examples"]) for entry in entries),
                "presets": entries[:limit],
            },
            "index": index,
        })

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
