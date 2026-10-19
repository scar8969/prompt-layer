"""System status and the public configuration surface the client reads at boot."""

from __future__ import annotations

from typing import Any

from ..http import RequestContext, Response


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def status(request: RequestContext) -> Response:
        """Everything the client needs to render honestly, and no secret.

        Public on purpose: the sign-in screen must be able to say whether this
        deployment is configured before anyone has an account.
        """
        return Response.json({"status": app.status()})

    def health(request: RequestContext) -> Response:
        """A liveness answer with no detail, for a container health check."""
        return Response.json({"ok": True, "database": app.db.ping()})

    router.add("GET", "/api/system/status", status, authenticated=False)
    router.add("GET", "/api/health", health, authenticated=False)
