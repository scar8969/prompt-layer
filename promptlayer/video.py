"""The generation layer: the exact requests a storyboard turns into.

This module does not generate video. It builds the request each scene becomes —
the endpoint, the method, the prompt, the endpoint's own parameters, and the image
field when a scene uses a reference frame — and it is a dry run unless three
environment variables are set:

    ALLOW_REMOTE_GENERATION=1   VIDEO_GATEWAY_URL   VIDEO_API_KEY

A key on its own is deliberately not enough. On a public deployment an API key in
the environment means any visitor can spend it, so spending has to be a decision
someone made on purpose, not a side effect of configuring a key. The gate is
separate from the compile gate for the same reason: enabling prompt expansion must
not silently enable paid generations.

Image fields are written as placeholders (`<attach image 1 (1280x720)>`) because
the pixels never reach this service — they are measured in the browser and stay
there.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .storyboard import planner_rules

GENERATE_PATH = "/v1/videos"


def generation_gate() -> Dict[str, Any]:
    """Whether this deployment may make an outbound generation call."""
    opt_in = os.getenv("ALLOW_REMOTE_GENERATION") == "1"
    url = bool(os.getenv("VIDEO_GATEWAY_URL"))
    key = bool(os.getenv("VIDEO_API_KEY"))
    missing = [name for name, present in (
        ("ALLOW_REMOTE_GENERATION=1", opt_in),
        ("VIDEO_GATEWAY_URL", url),
        ("VIDEO_API_KEY", key),
    ) if not present]
    return {
        "enabled": opt_in and url and key,
        "missing": missing,
        "reason": ("enabled: this deployment was explicitly allowed to spend on generations"
                   if opt_in and url and key else
                   "dry run only: " + ", ".join(missing) + " not set"),
    }


def _length_label(seconds: Any) -> str:
    """The clip length as the endpoint publishes it, without doubling the unit."""
    if seconds in (None, ""):
        return "?"
    text = str(seconds)
    return text if re.search(r"[a-z]", text, re.I) else text + "s"


def _image_placeholder(reference: Dict[str, Any], image: Optional[Dict[str, Any]]) -> str:
    if image:
        return f"<attach {image.get('name')} ({image.get('width')}x{image.get('height')})>"
    return f"<attach the {reference.get('role') or 'reference'} image>"


def _route(detail: Dict[str, Any]) -> Dict[str, Any]:
    """The method and path a job goes to, read from the dataset entry.

    Some entries carry a full route ("POST /v1/videos"); others carry a provider
    endpoint id (a fal endpoint slug). Both are kept verbatim — the plan reports
    where the request goes rather than normalising it into something it is not.
    """
    endpoint = str(detail.get("endpoint") or "")
    parts = endpoint.split(" ", 1)
    if len(parts) == 2 and parts[0] in ("GET", "POST", "PUT", "PATCH"):
        return {"method": parts[0], "path": parts[1], "endpoint": endpoint,
                "route": endpoint, "kind": "route"}
    return {"method": "POST", "path": GENERATE_PATH, "endpoint": endpoint or None,
            "route": f"POST {endpoint}" if endpoint else None, "kind": "endpoint_id"}


def build_plan(storyboard: Dict[str, Any],
               model_detail: Optional[Dict[str, Any]] = None,
               references: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One job per scene, with the request that scene becomes."""
    detail = model_detail or {}
    ref_conf = detail.get("reference") or {}
    images = {i.get("index"): i for i in (references or [])}
    route = _route(detail)
    jobs: List[Dict[str, Any]] = []

    for scene in storyboard.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        body: Dict[str, Any] = {"prompt": scene.get("prompt") or ""}
        if scene.get("negative"):
            body["negative_prompt"] = scene["negative"]
        for key, value in (scene.get("params") or {}).items():
            if key != "negative_prompt":
                body[key] = value
        # Remember the fields written for a reference: the image count is derived
        # from what actually went into the body, not by subtracting the documented
        # parameters — an edit endpoint documents its image field *as* a parameter,
        # so subtraction would report "no image" on the one endpoint that needs one.
        image_fields: List[str] = []
        for ref in scene.get("references") or []:
            field = str(ref_conf.get("field") or "image")
            # An endpoint may document two fields ("first_frame_image (and
            # last_frame_image)"); the role picks the right one.
            name = field
            if ref.get("role") == "last_frame" and "last_frame_image" in field:
                name = "last_frame_image"
            elif "first_frame_image" in field:
                name = "first_frame_image"
            elif "(" in field:
                name = field.split("(")[0].strip()
            body[name] = _image_placeholder(ref, images.get(ref.get("index")))
            if name not in image_fields:
                image_fields.append(name)
        jobs.append({
            "index": scene.get("index"),
            "scene": scene.get("id"),
            "title": scene.get("title"),
            "seconds": scene.get("seconds"),
            "provider": detail.get("provider"),
            "endpoint": route["endpoint"],
            "route": route["route"],
            "method": route["method"],
            "path": route["path"],
            "body": body,
            "image_fields": image_fields,
            "score": scene.get("score"),
            "note": (f"{len(body.get('prompt', '').split())} words · "
                     f"{_length_label(scene.get('seconds'))}"),
        })

    totals = storyboard.get("totals") or {}
    clips = totals.get("clips", len(jobs))
    rule = planner_rules()["one_beat_per_clip"]
    notes: List[Dict[str, Any]] = [{
        "id": "one_request_per_scene",
        "title": f"{clips} request(s), one per scene",
        "detail": "Each scene is a separate generation; the continuity is in the wording, "
                  "not in one long prompt.",
        "source": rule["source"],
        "evidence": rule["evidence"],
    }]
    if detail and not detail.get("endpoint"):
        notes.append({
            "id": "no_endpoint",
            "title": "This model has no endpoint in the dataset",
            "detail": "The request shape below is the generic one; check the provider's own schema.",
            "source": None,
            "evidence": None,
        })
    if route["kind"] == "endpoint_id":
        notes.append({
            "id": "endpoint_id_not_route",
            "title": "The dataset carries a provider endpoint id, not an HTTP route",
            "detail": f"{route['endpoint']} is a fal-style endpoint id. Where it is mounted is "
                      "your gateway's business; the body below is the schema's shape.",
            "source": None,
            "evidence": None,
        })
    if any(job["image_fields"] for job in jobs) and not ref_conf.get("field"):
        notes.append({
            "id": "image_field_unknown",
            "title": "An image field is needed but not documented for this endpoint",
            "detail": "Name the field yourself from the provider's schema before sending.",
            "source": None,
            "evidence": None,
        })

    gate = generation_gate()
    return {
        "model": storyboard.get("model"),
        "display": detail.get("display"),
        "provider": detail.get("provider"),
        "endpoint": route["endpoint"],
        "route": route["route"],
        "jobs": jobs,
        "totals": {
            "clips": clips,
            "seconds": totals.get("seconds"),
            "requests": len(jobs),
            "with_image": sum(1 for job in jobs if job["image_fields"]),
        },
        "notes": notes,
        "dry_run": True,
        "gate": gate,
    }


def submit_plan(plan: Dict[str, Any], *, confirm: bool = False) -> Dict[str, Any]:
    """Send the plan upstream — only when the deployment was explicitly allowed to.

    Every guard here is a refusal, never a fallback: no gate, no confirm, or no
    jobs all return a reason instead of a call.
    """
    gate = generation_gate()
    if not gate["enabled"]:
        return {"submitted": False, "reason": gate["reason"], "missing": gate["missing"]}
    if not confirm:
        return {"submitted": False,
                "reason": "confirm=true is required: this spends real generations"}
    jobs = plan.get("jobs") or []
    if not jobs:
        return {"submitted": False, "reason": "the plan has no jobs"}

    base = os.environ["VIDEO_GATEWAY_URL"].rstrip("/")
    results = []
    for job in jobs:
        request = urllib.request.Request(
            base + (job.get("path") or GENERATE_PATH),
            data=json.dumps(job["body"]).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + os.environ["VIDEO_API_KEY"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=110) as response:
                results.append({"scene": job["scene"], "status": response.status,
                                "body": json.loads(response.read().decode("utf-8", "ignore"))})
        except urllib.error.HTTPError as exc:
            results.append({"scene": job["scene"], "status": exc.code,
                            "error": exc.read().decode("utf-8", "ignore")[:300]})
        except Exception as exc:  # network, timeout, bad JSON
            results.append({"scene": job["scene"], "status": None, "error": str(exc)[:300]})
    return {"submitted": True, "results": results}
