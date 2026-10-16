"""POST /api/storyboard — talk a concept through, then turn it into scenes.

Body:
  {
    "action": "plan" | "scenes",
    "concept": "<the scene or the whole video concept>",
    "answers": {"camera_move": "dolly in", ...},     optional, fills the gaps
    "model": "<slug>",                               optional
    "design": {"selections": {...}, "palette": "...", "negative": [...],
               "template": "inline", "clean": true},  optional
    "beats": [{"text": "..."}],                      optional, your own edit
    "total_seconds": 24, "scene_seconds": 8, "aspect": "16x9",
    "references": [{"role": "first_frame", "width": 1280, ...}]
  }

`plan` is one chat turn: what was read out of the concept, what is missing, and the
next questions with the reason each one matters. `scenes` splits the concept into
beats and returns one prompt per beat, with the endpoint's own parameters.

The reading is a pattern match over the user's own words — no model call, nothing
invented. GET returns the facets, the reference roles and the published rules.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import (  # noqa: E402
    build_reference_set,
    build_scenes,
    facet_catalog,
    model_detail,
    plan,
    planner_rules,
    reference_roles,
    storyboard_version,
)
from promptlayer.http import read_json, send_error_json, send_json  # noqa: E402

MAX_CONCEPT = 8000
MAX_BEATS = 24


def _concept(handler, value):
    if not isinstance(value, str) or not value.strip():
        send_error_json(handler, 400, "Field 'concept' is required")
        return None
    if len(value) > MAX_CONCEPT:
        send_error_json(handler, 400, f"'concept' must be <= {MAX_CONCEPT} characters")
        return None
    return value


def _beats(value):
    """Accept the user's own beat list, so an edit in the UI is what gets used."""
    if not isinstance(value, list):
        return None
    out = []
    for entry in value[:MAX_BEATS]:
        if isinstance(entry, str) and entry.strip():
            out.append({"text": entry.strip(), "cue": False})
        elif isinstance(entry, dict) and str(entry.get("text") or "").strip():
            out.append({"text": str(entry["text"]).strip(), "cue": bool(entry.get("cue"))})
    return out or None


def _seconds(handler, value, field):
    """A clip length the caller sent, or None. A non-number is refused, not cast."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        send_error_json(handler, 400, f"Field '{field}' must be a number of seconds")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        send_error_json(handler, 400, f"Field '{field}' must be a number of seconds")
        return None
    if not 0 < number <= 600:
        send_error_json(handler, 400, f"Field '{field}' must be between 0 and 600 seconds")
        return None
    return number


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        send_json(self, 200, {
            "success": True,
            "version": storyboard_version(),
            "facets": facet_catalog(),
            "roles": reference_roles(),
            "rules": planner_rules(),
        })

    def do_POST(self) -> None:  # noqa: N802
        body = read_json(self)
        if body is None:
            return send_error_json(self, 400, "Body must be JSON under 64 KB")
        concept = _concept(self, body.get("concept"))
        if concept is None:
            return
        action = body.get("action") or "plan"
        if action not in ("plan", "scenes"):
            return send_error_json(self, 400, f"Unknown action '{action}'",
                                   available=["plan", "scenes"])
        model = body.get("model")
        if model is not None and not isinstance(model, str):
            return send_error_json(self, 400, "Field 'model' must be a string")
        detail = model_detail(model) if model else None
        if model and not detail:
            return send_error_json(self, 400, f"Unknown model '{model}'")
        # This is the video planner: its facets are camera moves, clip length,
        # dialogue and sound. Asked about an image or chat endpoint it would still
        # answer with questions and a scene, which is a storyboard for something
        # the endpoint cannot make. So it refuses instead of inventing one.
        if detail and detail.get("kind") != "video":
            return send_error_json(
                self, 400,
                f"'{model}' is a {detail.get('kind')} endpoint ({detail.get('endpoint')}) — "
                "this planner builds video scenes, so it does not apply. "
                "Pick a video endpoint, or use Preflight/Improve for this one.",
                kind=detail.get("kind"))

        if action == "plan":
            answers = body.get("answers")
            if answers is not None and not isinstance(answers, dict):
                return send_error_json(self, 400, "Field 'answers' must be an object")
            return send_json(self, 200, {
                "success": True,
                "data": plan(concept, answers=answers, model_detail=detail),
            })

        design = body.get("design")
        if design is not None and not isinstance(design, dict):
            return send_error_json(self, 400, "Field 'design' must be an object")
        # `/api/improve` takes `palette` at the top level; accept it here too
        # rather than silently ignoring a field the caller did send.
        if body.get("palette") and not (design or {}).get("palette"):
            design = {**(design or {}), "palette": body["palette"]}
        if body.get("template") and not (design or {}).get("template"):
            design = {**(design or {}), "template": body["template"]}
        answers = body.get("answers")
        if answers is not None and not isinstance(answers, dict):
            return send_error_json(self, 400, "Field 'answers' must be an object")
        references = build_reference_set(body.get("references"), detail) if body.get("references") else None
        total_seconds = _seconds(self, body.get("total_seconds"), "total_seconds")
        scene_seconds = _seconds(self, body.get("scene_seconds"), "scene_seconds")
        if (body.get("total_seconds") not in (None, "") and total_seconds is None) or \
           (body.get("scene_seconds") not in (None, "") and scene_seconds is None):
            return
        data = build_scenes(
            concept,
            model=model,
            model_detail=detail,
            design=design,
            beats=_beats(body.get("beats")),
            total_seconds=total_seconds,
            scene_seconds=scene_seconds,
            aspect=body.get("aspect"),
            references=(references or {}).get("images"),
            answers=answers,
            template=(design or {}).get("template") or "inline",
        )
        if references:
            data["reference_set"] = references
        send_json(self, 200, {"success": True, "data": data})

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
