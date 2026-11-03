"""Conversation: one turn at a time, on the workspace's own key.

  GET  /api/chat/models   the conversation models this workspace can use
  POST /api/chat          send one conversation and return the provider's answer

There is no queue here, on purpose: a chat completion is synchronous and returns
text, not an asset. The route is bounded, the model must be a registered
conversation model, and the provider's error is passed through rather than
rewritten into a plausible answer.
"""

from __future__ import annotations

from typing import Any

from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def models(request: RequestContext) -> Response:
        """Conversation models, with the workspace default first.

        Only models registered as `other` are listed: an image model cannot hold
        a conversation, and offering it here would be a picker full of failures.
        """
        rows = app.repos.models.list(
            request.session.workspace_id, modality="other", enabled=True
        )
        shaped = [_shape.model(app.repos.models.with_provider(request.session.workspace_id, str(item["id"]))) for item in rows]
        default = next((item for item in shaped if item.get("is_default")), None)
        return Response.json(
            {
                "models": shaped,
                "default_model_id": str(default.get("id") or "") if default else "",
                "notes": {
                    "billing": "Each turn is billed by your provider account, not by this platform.",
                },
            }
        )

    def send(request: RequestContext) -> Response:
        """Send one conversation. Returns the assistant message and its usage."""
        return Response.json(app.chat.complete(request.session, request.json()))

    router.add("GET", "/api/chat/models", models)
    router.add("POST", "/api/chat", send)
