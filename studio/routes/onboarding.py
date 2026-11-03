"""One-key onboarding: discover a provider's catalogue, then register it.

  POST /api/onboarding/discover   read + classify, probe chat models, write nothing
  POST /api/onboarding/register   create the connection and the chosen models

Discovery is deliberately a separate request from registration. The user sees a
list with a verdict per model *before* anything is stored, which is the only way
"test all the models" can mean something without spending silently.
"""

from __future__ import annotations

from typing import Any

from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def discover(request: RequestContext) -> Response:
        """Read the endpoint's model list and probe its conversation models.

        Writes nothing. The response is the document the register step accepts, so
        what the user confirms is exactly what was tested.
        """
        plan = app.onboarding.discover(request.session, request.json())
        return Response.json({"plan": plan})

    def register(request: RequestContext) -> Response:
        """Create the connection and the chosen models."""
        result = app.onboarding.register(request.session, request.json())
        return Response.json(
            {
                "connection": _shape.connection(result["connection"]),
                "connections": [
                    _shape.connection(item) for item in result.get("connections") or []
                ],
                "models": [_shape.model(item) for item in result["models"]],
                "default_chat_model_id": result["default_chat_model_id"],
                "default_chat": result["default_chat"],
                "notes": result["notes"],
            },
            status=201,
        )

    router.add("POST", "/api/onboarding/discover", discover)
    router.add("POST", "/api/onboarding/register", register)
