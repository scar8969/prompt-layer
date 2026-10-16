"""POST /api/video — the generation layer: the requests a storyboard becomes.

Body:
  {
    "action": "plan" | "submit",
    "storyboard": { ...the output of /api/storyboard... },   or
    "concept": "...", plus the same fields /api/storyboard takes,
    "model": "<slug>",
    "references": [...],
    "confirm": true                                          required to submit
  }

`plan` is always a dry run: it returns one job per scene with the endpoint, the
method, the body, and the parameters that endpoint documents. It does not generate
video and it does not call anything.

`submit` sends the plan only when the deployment was explicitly allowed to spend:
ALLOW_REMOTE_GENERATION=1 and VIDEO_GATEWAY_URL and VIDEO_API_KEY. A key alone is
not enough — on a public deployment that would let any visitor spend it. Every
guard refuses rather than falling back.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import (  # noqa: E402
    build_plan,
    build_reference_set,
    build_scenes,
    generation_gate,
    model_detail,
)
from promptlayer.http import read_json, send_error_json, send_json  # noqa: E402


def _storyboard(handler, body):
    """Either the client's storyboard, or one built from the same inputs."""
    supplied = body.get("storyboard")
    if isinstance(supplied, dict) and supplied.get("scenes"):
        return supplied
    concept = body.get("concept")
    if not isinstance(concept, str) or not concept.strip():
        send_error_json(handler, 400,
                        "Send either 'storyboard' (from /api/storyboard) or 'concept'")
        return None
    model = body.get("model")
    detail = model_detail(model) if isinstance(model, str) and model else None
    design = body.get("design") if isinstance(body.get("design"), dict) else None
    return build_scenes(concept, model=model, model_detail=detail, design=design,
                        total_seconds=body.get("total_seconds"),
                        scene_seconds=body.get("scene_seconds"),
                        aspect=body.get("aspect"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        send_json(self, 200, {"success": True, "gate": generation_gate()})

    def do_POST(self) -> None:  # noqa: N802
        body = read_json(self)
        if body is None:
            return send_error_json(self, 400, "Body must be JSON under 64 KB")
        action = body.get("action") or "plan"
        if action not in ("plan", "submit"):
            return send_error_json(self, 400, f"Unknown action '{action}'",
                                   available=["plan", "submit"])
        storyboard = _storyboard(self, body)
        if storyboard is None:
            return

        model = body.get("model") or storyboard.get("model")
        detail = model_detail(model) if isinstance(model, str) and model else None
        if model and not detail:
            return send_error_json(self, 400, f"Unknown model '{model}'")
        references = body.get("references")
        ref_set = build_reference_set(references, detail) if references else None

        plan = build_plan(storyboard, detail, references=(ref_set or {}).get("images"))
        if ref_set:
            plan["reference_set"] = ref_set

        if action == "plan":
            return send_json(self, 200, {"success": True, "data": plan})

        # A submit is never implicit: it needs the gate, the confirm flag, and jobs.
        gate = generation_gate()
        if not gate["enabled"]:
            plan["submitted"] = False
            plan["reason"] = gate["reason"]
            return send_json(self, 200, {"success": True, "data": plan})
        if not body.get("confirm"):
            plan["submitted"] = False
            plan["reason"] = "confirm=true is required: this spends real generations"
            return send_json(self, 200, {"success": True, "data": plan})

        from promptlayer import submit_plan  # local import keeps the dry path free
        result = submit_plan(plan, confirm=True)
        plan["submitted"] = result.get("submitted")
        plan["results"] = result.get("results")
        plan["reason"] = result.get("reason")
        send_json(self, 200, {"success": True, "data": plan})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
