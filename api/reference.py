"""POST /api/reference — check a set of reference images against an endpoint.

Body:
  {
    "model": "<slug>",                       optional
    "images": [{"name": "frame.png", "role": "first_frame", "width": 1280,
                "height": 720, "type": "image/png", "bytes": 240000,
                "palette": ["#0a1b2c", "#f0e0d0"], "scene": 1}]
  }

The pixels never reach this service. The browser measures each image — dimensions,
MIME type, byte size and the dominant colours read off a canvas — and sends only
those numbers, which is everything the documented checks need.

GET returns the roles an image can take and the rules that change once one is
attached, each quoted from the model owner that published it.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import (  # noqa: E402
    MAX_IMAGES,
    build_reference_set,
    load_design,
    model_detail,
    reference_roles,
)
from promptlayer.http import read_json, send_error_json, send_json  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        design = load_design()
        send_json(self, 200, {
            "success": True,
            "roles": reference_roles(),
            "max_images": MAX_IMAGES,
            "rules": design["image_reference"]["rules"],
            "source": design["image_reference"]["source"],
            "evidence": design["image_reference"]["evidence"],
        })

    def do_POST(self) -> None:  # noqa: N802
        body = read_json(self)
        if body is None:
            return send_error_json(self, 400, "Body must be JSON under 64 KB")
        images = body.get("images")
        if images is not None and not isinstance(images, list):
            return send_error_json(self, 400, "Field 'images' must be a list")
        model = body.get("model")
        detail = model_detail(model) if isinstance(model, str) and model else None
        if model and not detail:
            return send_error_json(self, 400, f"Unknown model '{model}'")
        send_json(self, 200, {"success": True,
                              "data": build_reference_set(images, detail)})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
