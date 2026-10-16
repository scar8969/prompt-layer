"""GET /api/models — the models we hold facts for, and the expander styles.

`GET /api/models?slug=<slug>` returns one model's layers, parameters and sources.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import (  # noqa: E402
    dataset_version,
    design_version,
    expander_styles,
    facet_catalog,
    generation_gate,
    load_design,
    model_detail,
    model_list,
    planner_rules,
    preset_index,
    reference_roles,
    storyboard_version,
)
from promptlayer.http import send_error_json, send_json  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        slug = (query.get("slug") or [""])[0]
        if slug:
            detail = model_detail(slug)
            if not detail:
                return send_error_json(self, 404, f"Unknown model '{slug}'", known=model_list())
            return send_json(self, 200, {"success": True, "data": detail})
        send_json(self, 200, {
            "success": True,
            "data": model_list(),
            "expanders": expander_styles(),
            "version": dataset_version(),
            # The improvement vocabulary and templates, so the UI can offer the
            # same sourced options the lint understands.
            "design": {
                "version": design_version(),
                "vocab": load_design()["vocab"],
                "templates": load_design()["templates"],
                "sources": load_design()["sources"],
            },
            # Hugging Face's own widget presets: counts + where they came from.
            "presets": preset_index(),
            # The scene planner: the facets a video prompt needs, the roles a
            # reference image can take, and the published rules behind both.
            "storyboard": {
                "version": storyboard_version(),
                "facets": facet_catalog(),
                "roles": reference_roles(),
                "rules": planner_rules(),
            },
            "generation": generation_gate(),
        })

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
