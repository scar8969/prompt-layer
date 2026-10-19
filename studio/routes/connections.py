"""BYOK/BYOM connections: list, create, edit, test, rotate the key, delete."""

from __future__ import annotations

from typing import Any

from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def index(request: RequestContext) -> Response:
        """Every connection this workspace owns, with its credential fingerprint."""
        rows = app.repos.connections.list(request.session.workspace_id)
        return Response.json({"connections": _shape.connections(rows)})

    def types(request: RequestContext) -> Response:
        """The provider types the wizard offers, from the live adapter registry."""
        return Response.json({"types": app.connections.types()})

    def create(request: RequestContext) -> Response:
        """Create a connection and seal its key. The key is not echoed back."""
        result = app.connections.create(request.session, request.json())
        return Response.json(
            {
                "connection": _shape.connection(result["connection"]),
                "models": [_shape.model(item) for item in result.get("models") or ()],
            },
            status=201,
        )

    def show(request: RequestContext) -> Response:
        """One connection, without any secret in it."""
        found = app.repos.connections.get(request.session.workspace_id, request.params["id"])
        return Response.json({"connection": _shape.connection(found)})

    def update(request: RequestContext) -> Response:
        """Change the settings of a connection. The credential is untouched."""
        result = app.connections.update(request.session, request.params["id"], request.json())
        return Response.json({"connection": _shape.connection(result["connection"])})

    def destroy(request: RequestContext) -> Response:
        """Delete a connection, its key, and the usability of its models."""
        result = app.connections.delete(request.session, request.params["id"])
        return Response.json(dict(result, ok=True))

    def test(request: RequestContext) -> Response:
        """Make one real call and report exactly what the provider said."""
        return Response.json(app.connections.test(request.session, request.params["id"]))

    def rotate(request: RequestContext) -> Response:
        """Replace the credential without rebuilding the connection."""
        result = app.connections.rotate_key(request.session, request.params["id"], request.json())
        return Response.json({"connection": _shape.connection(result["connection"])})

    def models(request: RequestContext) -> Response:
        """The models registered against this connection."""
        rows = app.repos.models.list(
            request.session.workspace_id, connection_id=request.params["id"]
        )
        return Response.json({"models": [_shape.model(item) for item in rows]})

    def add_model(request: RequestContext) -> Response:
        """Register one model, with the capabilities the user declared."""
        model = app.connections.add_model(request.session, request.params["id"], request.json())
        return Response.json({"model": _shape.model(model)}, status=201)

    def discover(request: RequestContext) -> Response:
        """Ask the provider what it lists. Nothing is registered automatically."""
        return Response.json(app.connections.discover_models(request.session, request.params["id"]))

    def suggested(request: RequestContext) -> Response:
        """What the provider itself states about one model, if it states anything."""
        return Response.json(
            app.connections.suggested_capabilities(
                request.session, request.params["id"], request.arg("model_key")
            )
        )

    router.add("GET", "/api/connections", index)
    router.add("GET", "/api/connections/types", types)
    router.add("POST", "/api/connections", create)
    router.add("GET", "/api/connections/{id}", show)
    router.add("PATCH", "/api/connections/{id}", update)
    router.add("DELETE", "/api/connections/{id}", destroy)
    router.add("POST", "/api/connections/{id}/test", test)
    router.add("POST", "/api/connections/{id}/rotate-key", rotate)
    router.add("GET", "/api/connections/{id}/models", models)
    router.add("POST", "/api/connections/{id}/models", add_model)
    router.add("GET", "/api/connections/{id}/discover", discover)
    router.add("GET", "/api/connections/{id}/suggested", suggested)
