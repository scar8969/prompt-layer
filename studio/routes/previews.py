"""Rendered previews for catalogue presets.

A Higgsfield-style wall is pictures. This catalogue has sentences, and it refuses
to ship a photograph it did not produce. The compromise is honest and opt-in: a
workspace can spend its own provider key to render **one real frame per preset**,
and that frame becomes the tile. Until it does, the tile is the deterministic
style card.

Nothing here is a new object type. A preview is an ordinary generation job with
`preset_key` set (see migration 0008), so it runs through the same queue, the
same adapters, the same cost records and the same failure reporting. The routes
below only answer two questions: "render this preset" and "which renders exist".

  POST /api/presets/{id}/preview      render one preset on a connected model
  GET  /api/presets/{id}/previews     every render attempted for it
  GET  /api/previews?keys=a,b,c       the latest render for many presets
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..adapters.base import parse_parameter_schema
from ..errors import NOT_FOUND, VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from ..presets_data import preset as builtin_preset

#: How many presets one gallery request may resolve in a single call.
MAX_PREVIEW_KEYS = 120


def _preset_document(app: Any, workspace_id: str, preset_id: str) -> Dict[str, Any]:
    """The preset as one document, whichever tier it came from.

    A built-in is a catalogue entry with `parameters`; a saved preset is a row with
    `defaults`. Both are normalised to the same four facts the renderer needs, and
    an unknown id is refused as not-found rather than rendered as an empty prompt.
    """
    if preset_id.startswith("builtin:"):
        entry = builtin_preset(preset_id.split(":", 1)[1])
        if entry is None:
            raise ApiError(NOT_FOUND, "No catalogue preset has that identifier.")
        parameters = dict(entry.get("parameters") or {})
        aspect = str(entry.get("aspect_ratio") or "")
        return {
            "id": preset_id,
            "name": str(entry.get("name") or ""),
            "kind": str(entry.get("modality") or "image"),
            "prompt": str(entry.get("prompt_template") or ""),
            "negative": str(entry.get("negative_template") or ""),
            "parameters": parameters,
            "aspect_ratio": aspect,
        }

    row = app.repos.library.get_preset(workspace_id, preset_id)
    defaults = dict(row.get("defaults") or {})
    aspect = str(defaults.pop("aspect_ratio", "") or "")
    return {
        "id": preset_id,
        "name": str(row.get("name") or ""),
        "kind": str(row.get("kind") or "image"),
        "prompt": str(row.get("prompt_template") or ""),
        "negative": str(row.get("negative_prompt_template") or ""),
        "parameters": defaults,
        "aspect_ratio": aspect,
    }


def _accepted_parameters(model: Mapping[str, Any], candidates: Mapping[str, Any]) -> Dict[str, Any]:
    """Only the preset settings this model actually declares.

    A preset is a document written for the catalogue, not for one provider. The
    studio already filters them client-side; the server must do the same, or a
    preset carrying `guidance_scale` would be refused by a model that has no such
    field and the user would see a 400 instead of a render.
    """
    schema = model.get("parameter_schema") or {}
    if isinstance(schema, str):
        import json

        try:
            schema = json.loads(schema)
        except ValueError:
            schema = {}
    parsed = parse_parameter_schema(schema)
    known = set(parsed.names())
    return {name: value for name, value in candidates.items() if name in known}


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def render(request: RequestContext) -> Response:
        """Queue one real render for a preset on a model the workspace connected.

        The caller names the model; the server never picks one silently, because a
        preview spends real money on a real account. `force` re-renders even when a
        completed preview already exists; without it the route is idempotent per
        preset and model, which keeps a double-click from buying two frames.
        """
        request.session.require("generation:create")
        preset_id = str(request.params["id"])
        body = request.json()
        model_id = str(body.get("model_id") or "").strip()
        if not model_id:
            raise ApiError(
                VALIDATION_FAILED,
                "Choose the model that should render this preview.",
                field="model_id",
                hint="A preview uses your own key, so it needs a connected model.",
            )
        preset = _preset_document(app, request.session.workspace_id, preset_id)
        model = app.repos.models.get(request.session.workspace_id, model_id)
        modality = str(model.get("modality") or "image")
        if modality not in ("image", "video"):
            raise ApiError(
                VALIDATION_FAILED,
                "A preview needs an image or video model.",
                field="model_id",
            )
        if modality != preset["kind"]:
            raise ApiError(
                VALIDATION_FAILED,
                "This preset is a %s look, but the chosen model is a %s model."
                % (preset["kind"], modality),
                field="model_id",
            )
        mode = "text_to_image" if modality == "image" else "text_to_video"
        declared = [str(item) for item in (model.get("modes") or [])]
        if declared and mode not in declared:
            raise ApiError(
                "capability_unsupported",
                "The chosen model does not declare %s." % mode.replace("_", " "),
                field="model_id",
                details={"supported": declared},
            )

        payload: Dict[str, Any] = {
            "model_id": model_id,
            "prompt": preset["prompt"],
            "mode": mode,
            "count": 1,
            "parameters": _accepted_parameters(model, preset["parameters"]),
            "preset_key": preset_id,
            # Deliberately unique per submission: a preview is a fresh render even
            # when the same preset/model pair already has one.
            "client_request_id": "preview:%s:%s" % (preset_id, __import__("uuid").uuid4().hex),
        }
        if preset["negative"]:
            payload["negative_prompt"] = preset["negative"]
        if preset["aspect_ratio"]:
            payload["aspect_ratio"] = preset["aspect_ratio"]

        result = app.generations.queue(
            request.session, payload, modality=modality, mode=mode
        )
        job = result.get("job") or {}
        return Response.json(
            {
                "job": app.repos.jobs.get(
                    request.session.workspace_id, str(job.get("id") or "")
                ),
                "deduplicated": bool(result.get("deduplicated")),
                "preset_id": preset_id,
            },
            status=202,
        )

    def list_for_preset(request: RequestContext) -> Response:
        """Every preview attempt for one preset, newest first."""
        preset_id = str(request.params["id"])
        # Resolve the preset first: a preview for a preset this workspace cannot
        # see should read as not-found, not as "no renders yet".
        _preset_document(app, request.session.workspace_id, preset_id)
        previews = app.repos.jobs.list_previews(
            request.session.workspace_id, preset_id, limit=20
        )
        return Response.json({"previews": previews, "preset_id": preset_id})

    def latest(request: RequestContext) -> Response:
        """The latest render per preset, for a grid that already has the ids."""
        raw = request.arg("keys") or ""
        keys = [item.strip() for item in raw.split(",") if item.strip()]
        if len(keys) > MAX_PREVIEW_KEYS:
            raise ApiError(
                VALIDATION_FAILED,
                "Ask for at most %d presets at a time." % MAX_PREVIEW_KEYS,
                field="keys",
            )
        previews = app.repos.jobs.preview_map(request.session.workspace_id, keys)
        return Response.json({"previews": previews})

    router.add("POST", "/api/presets/{id}/preview", render)
    router.add("GET", "/api/presets/{id}/previews", list_for_preset)
    router.add("GET", "/api/previews", latest)
