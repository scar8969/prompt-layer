"""POST /api/improve — how the output can be improved, for this prompt and model.

Body:
  {
    "model": "<slug>",           required
    "prompt": "<text>",          required
    "image": {"count": 1, "width": 1280, "height": 720, "type": "image/png", "bytes": 240000},
    "selections": {"camera_move": "dolly in", "light_source": "moonlight", ...},
    "palette": "amber, cream, walnut brown",
    "template": "inline" | "labelled"
  }

Returns the findings for what you wrote, the merged prompt, exactly what the merge
added with a source for each element, and the score delta between the two.

Nothing is uploaded anywhere: `image` carries only what the browser measured
locally, which is all the reference checks need.
"""

from __future__ import annotations

import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import build_improve, load_design, model_detail  # noqa: E402
from promptlayer.http import read_json, send_error_json, send_json, validate_prompt  # noqa: E402

MAX_IMAGE_DIM = 8192

def _clean_image(raw) -> tuple:
    """Validate the client's own measurements. Returns (image dict, count)."""
    if not isinstance(raw, dict):
        return {}, 0
    try:
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)
        count = int(raw.get("count") or 0)
    except (TypeError, ValueError):
        return {}, 0
    if not (0 <= width <= MAX_IMAGE_DIM and 0 <= height <= MAX_IMAGE_DIM) or not (0 <= count <= 4):
        return {}, 0
    return {
        "count": count,
        "width": width,
        "height": height,
        "type": str(raw.get("type") or "")[:40],
        "bytes": int(raw.get("bytes") or 0),
    }, count


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
        selections = body.get("selections") or {}
        if not isinstance(selections, dict):
            return send_error_json(self, 400, "Field 'selections' must be an object")
        template = body.get("template") or "inline"
        if template not in load_design()["templates"]:
            return send_error_json(self, 400, f"Unknown template '{template}'",
                                   available=list(load_design()["templates"]))
        clean = bool(body.get("clean"))

        detail = model_detail(model)
        image, image_count = _clean_image(body.get("image"))

        try:
            result = build_improve(
                prompt=prompt,
                selections=selections,
                template=template,
                palette=body.get("palette"),
                model_layers=(detail or {}).get("layers"),
                image_count=image_count,
                accepts_image=bool((detail or {}).get("accepts_image")),
                clean=clean,
            )
        except KeyError as exc:  # defensive: template already validated above
            return send_error_json(self, 400, f"Unknown option: {exc}")

        # Reference-image checks that need dimensions, not just presence.
        reference_notes = []
        ref = (detail or {}).get("reference")
        if image_count and ref:
            reference_notes.append({
                "id": "reference_field",
                "detail": f"Send it as: {ref.get('field', 'the image field')}. {ref.get('constraints', '')}".strip(),
                "source": ref.get("source"),
                "evidence": ref.get("evidence"),
            })
            expected = ((detail or {}).get("params", {}).get("size") or {}).get("default", "")
            if image["width"] and image["height"] and "x" in expected:
                # A documented size list ("720x1280 or 1280x720") passes on any match.
                mine = f"{image['width']}x{image['height']}"
                allowed = [part.strip() for part in re.split(r"\s+or\s+", expected)]
                if mine not in allowed:
                    reference_notes.append({
                        "id": "reference_size",
                        "severity": "warn",
                        "detail": f"Your image is {mine}; this endpoint documents {expected}. A mismatch is rejected rather than resized.",
                        "source": ref.get("source"),
                        "evidence": ref.get("evidence"),
                    })
            if image["type"] and image["type"] not in ("image/jpeg", "image/png", "image/webp"):
                reference_notes.append({
                    "id": "reference_type",
                    "severity": "warn",
                    "detail": f"{image['type']} is not in the documented set (image/jpeg, image/png, image/webp).",
                    "source": ref.get("source"),
                    "evidence": ref.get("evidence"),
                })

        result["model"] = model
        result["image"] = image
        result["reference_notes"] = reference_notes
        send_json(self, 200, {"success": True, "data": result})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
