"""Factories: plan a shoot, then store it as a canvas.

  GET  /api/factories             the factories and what each produces
  POST /api/factories/plan        plan one factory run; stores nothing, spends nothing
  POST /api/factories/canvases    save the plan as a canvas the canvas screen can run

The split matters. A plan is a document a person can read and reject before any
money is spent; creating the canvas is still free; only running it queues jobs.
Nothing here contacts a provider.
"""

from __future__ import annotations

from typing import Any, Dict

from .. import factories
from ..errors import VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from . import _shape

#: The plan fields a caller may send. Kept as a tuple so an unknown field is
#: refused by name rather than silently dropped from a plan someone will run.
PLAN_FIELDS = (
    "kind",
    "model_id",
    "product",
    "benefit",
    "audience",
    "subject",
    "body",
    "lens",
    "lighting",
    "palette",
    "styles",
)


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def index(request: RequestContext) -> Response:
        """The factories, with the number of shots each produces."""
        return Response.json(
            {
                "factories": factories.describe()["factories"],
                "notes": {
                    "spend": "Planning and saving a canvas cost nothing; running it queues real jobs.",
                },
            }
        )

    def plan(request: RequestContext) -> Response:
        """Plan one factory run. Pure: the same brief produces the same graph."""
        body = request.json()
        unknown = sorted(key for key in body if key not in PLAN_FIELDS)
        if unknown:
            raise ApiError(
                VALIDATION_FAILED,
                "The plan contains fields this platform does not use: %s." % ", ".join(unknown),
                details={"unknown_fields": unknown, "allowed": list(PLAN_FIELDS)},
            )
        # Resolve the model through the workspace so a plan cannot name a model
        # that does not exist here; the capability check happens when the canvas
        # is run, where the queue already enforces it.
        model_id = str(body.get("model_id") or "").strip()
        if model_id:
            app.repos.models.get(request.session.workspace_id, model_id)
        document = factories.plan(
            str(body.get("kind") or ""),
            model_id=model_id,
            product=str(body.get("product") or ""),
            benefit=str(body.get("benefit") or ""),
            audience=str(body.get("audience") or ""),
            subject=str(body.get("subject") or ""),
            body=str(body.get("body") or ""),
            lens=str(body.get("lens") or ""),
            lighting=str(body.get("lighting") or ""),
            palette=str(body.get("palette") or ""),
            styles=body.get("styles"),
        )
        return Response.json({"plan": document})

    def create_canvas(request: RequestContext) -> Response:
        """Save a factory plan as a canvas.

        The graph is re-planned here from the same body rather than trusted from
        the client, so a canvas cannot be stored with a node list that no factory
        would produce. The name defaults to the factory and the product.
        """
        request.session.require("project:manage")
        body = dict(request.json())
        name = str(body.pop("name", "") or "").strip()
        unknown = sorted(key for key in body if key not in PLAN_FIELDS)
        if unknown:
            raise ApiError(
                VALIDATION_FAILED,
                "The plan contains fields this platform does not use: %s." % ", ".join(unknown),
                details={"unknown_fields": unknown, "allowed": list(PLAN_FIELDS)},
            )
        model_id = str(body.get("model_id") or "").strip()
        if model_id:
            app.repos.models.get(request.session.workspace_id, model_id)
        document = factories.plan(
            str(body.get("kind") or ""),
            model_id=model_id,
            product=str(body.get("product") or ""),
            benefit=str(body.get("benefit") or ""),
            audience=str(body.get("audience") or ""),
            subject=str(body.get("subject") or ""),
            body=str(body.get("body") or ""),
            lens=str(body.get("lens") or ""),
            lighting=str(body.get("lighting") or ""),
            palette=str(body.get("palette") or ""),
            styles=body.get("styles"),
        )
        canvas = app.repos.canvases.create(
            request.session.workspace_id,
            request.session.user_id,
            {
                "name": name or "%s — %s" % (document["label"], document["product"]),
                "description": "Planned by the %s factory from a brief." % document["label"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        shaped = _shape.row(canvas)
        shaped["summary"] = document["summary"]
        return Response.json({"canvas": shaped, "plan": document}, status=201)

    router.add("GET", "/api/factories", index)
    router.add("POST", "/api/factories/plan", plan)
    router.add("POST", "/api/factories/canvases", create_canvas)
