"""POST /api/preflight — what the provider will receive, and what it will add.

Body: {model, prompt, options?, system_message?, image_count?}
Returns the assembled payload, every resolved parameter with its source, each
layer that touches the request with the knob that turns it off, and the source +
verbatim evidence for every claim.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import build_preflight, model_list  # noqa: E402
from promptlayer.http import read_json, send_error_json, send_json, validate_prompt  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = read_json(self)
        if body is None:
            return send_error_json(self, 400, "Body must be JSON under 64 KB")
        prompt = validate_prompt(self, body.get("prompt"))
        if prompt is None:
            return
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return send_error_json(self, 400, "Field 'model' is required")
        options = body.get("options") or {}
        if not isinstance(options, dict):
            return send_error_json(self, 400, "Field 'options' must be an object")
        known = {m["slug"] for m in model_list()}
        if model not in known:
            # Not an error: unknown models still get the generic layers, and the
            # response says so. Refusing here would look like the endpoint is
            # broken rather than like we simply have no facts for that model.
            pass
        result = build_preflight(
            model_slug=model,
            prompt=prompt,
            options=options,
            system_message=body.get("system_message"),
            image_count=int(body.get("image_count") or 0),
        )
        send_json(self, 200, {"success": True, "data": result})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
