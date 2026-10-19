"""The model registry: what a workspace can generate with, and what each can do."""

from __future__ import annotations

from typing import Any

from ..adapters.base import CAMERA_MOVES, REFERENCE_ROLES, describe_parameter_types, mode_label
from ..adapters.types import CapabilityProfile
from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def index(request: RequestContext) -> Response:
        """Every model in this workspace, filtered on demand."""
        workspace_id = request.session.workspace_id
        rows = app.repos.models.list(
            workspace_id,
            modality=request.arg("modality") or None,
            connection_id=request.arg("connection_id") or None,
            source=request.arg("source") or None,
            favorite=request.flag("favorite"),
            enabled=request.flag("enabled"),
            q=request.arg("q") or None,
        )
        return Response.json(
            {
                "models": [
                    _shape.model(app.repos.models.with_provider(workspace_id, str(item["id"])))
                    for item in rows
                ]
            }
        )

    def show(request: RequestContext) -> Response:
        """One model with its connection attached."""
        workspace_id = request.session.workspace_id
        model = app.repos.models.get(workspace_id, request.params["id"])
        return Response.json(
            {"model": _shape.model(app.repos.models.with_provider(workspace_id, str(model["id"])))}
        )

    def capabilities(request: RequestContext) -> Response:
        """What this model can do, as enabled and disabled controls with reasons.

        A mode the model does not declare is returned with supported false and the
        sentence the UI shows beside the disabled control. Nothing is inferred from
        the modality: an image model is not assumed to support inpainting.
        """
        model = app.repos.models.get(request.session.workspace_id, request.params["id"])
        declared = [str(item) for item in (model.get("modes") or [])]
        profile = CapabilityProfile.from_public(
            dict(
                model.get("capabilities") or {},
                modality=str(model.get("modality") or "image"),
                modes=tuple(declared),
            )
        )
        from ..adapters.base import IMAGE_MODES, VIDEO_MODES

        vocabulary = IMAGE_MODES if model.get("modality") == "image" else VIDEO_MODES
        supported = set(declared)
        return Response.json(
            {
                "capabilities": profile.to_public(),
                "modes": [
                    {
                        "key": key,
                        "label": mode_label(key),
                        "supported": key in supported,
                        "reason": ""
                        if key in supported
                        else "This model does not declare %s." % mode_label(key),
                    }
                    for key in vocabulary
                ],
                "camera_moves": [
                    {
                        "key": move.key,
                        "label": move.label,
                        "phrase": move.phrase,
                        "is_native": move.is_native,
                        "note": ""
                        if move.is_native
                        else "Sent as prompt wording; this model has no camera field.",
                    }
                    for move in CAMERA_MOVES
                ],
                "reference_roles": list(REFERENCE_ROLES),
            }
        )

    def parameters(request: RequestContext) -> Response:
        """The parameter schema the studio renders, and the types it may use."""
        from ..adapters.base import parse_parameter_schema

        model = app.repos.models.get(request.session.workspace_id, request.params["id"])
        schema = parse_parameter_schema(model.get("parameter_schema") or {})
        return Response.json(
            {
                "schema": schema.to_public(),
                "defaults": model.get("defaults") or {},
                "parameter_types": describe_parameter_types(),
            }
        )

    def update(request: RequestContext) -> Response:
        """Change the display name, the flags, or the declared capabilities."""
        request.session.require("model:manage")
        body = request.json()
        fields = {}
        for name in (
            "display_name",
            "is_enabled",
            "is_favorite",
            "modes",
            "capabilities",
            "parameter_schema",
            "defaults",
            "limits",
        ):
            if name in body:
                fields[name] = body[name]
        model = app.repos.models.update(request.session.workspace_id, request.params["id"], fields)
        return Response.json(
            {"model": _shape.model(app.repos.models.with_provider(request.session.workspace_id, str(model["id"])))}
        )

    def favorite(request: RequestContext) -> Response:
        """Mark or unmark a model as a favourite, for the studio picker."""
        body = request.json()
        model = app.repos.models.set_favorite(
            request.session.workspace_id,
            request.params["id"],
            bool(body.get("favorite", True)),
        )
        return Response.json({"model": _shape.model(model)})

    def destroy(request: RequestContext) -> Response:
        """Remove a model from the registry. Jobs keep their own snapshot."""
        request.session.require("model:manage")
        app.repos.models.delete(request.session.workspace_id, request.params["id"])
        return Response.json({"ok": True})

    router.add("GET", "/api/models", index)
    router.add("GET", "/api/models/{id}", show)
    router.add("GET", "/api/models/{id}/capabilities", capabilities)
    router.add("GET", "/api/models/{id}/parameters", parameters)
    router.add("PATCH", "/api/models/{id}", update)
    router.add("POST", "/api/models/{id}/favorite", favorite)
    router.add("DELETE", "/api/models/{id}", destroy)
