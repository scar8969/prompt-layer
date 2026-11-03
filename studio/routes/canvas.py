"""Saved canvases: documents and the runs that execute them.

  GET    /api/canvases                  the workspace's canvases
  POST   /api/canvases                  save one, graph validated
  GET    /api/canvases/{id}             one canvas, with its execution order
  PATCH  /api/canvases/{id}             edit it, graph re-validated
  DELETE /api/canvases/{id}             remove it, history kept
  POST   /api/canvases/{id}/runs        open a run and queue what is ready
  GET    /api/canvases/{id}/runs        recent runs
  GET    /api/canvases/{id}/runs/{runId} one run, advanced by the read

The last route is a GET that does work, and that is deliberate: it is the same
pattern the job route uses, for the same reason. On a platform that may freeze a
process the moment a response is written, a poll that advances the thing it is
polling is the only reliable clock. Claiming and queueing are idempotent, so two
polls of the same run cannot buy the same frame twice.
"""

from __future__ import annotations

from typing import Any

from .. import canvas as canvas_document
from ..http import RequestContext, Response
from . import _shape


def _canvas(app: Any, workspace_id: str, canvas_id: str) -> dict:
    """A canvas row plus the summary derived from its own graph."""
    row = app.repos.canvases.get(workspace_id, canvas_id)
    out = _shape.row(row)
    out["summary"] = canvas_document.describe(
        out.get("nodes") or [], out.get("edges") or []
    )
    return out


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def index(request: RequestContext) -> Response:
        """Every live canvas in this workspace."""
        rows = app.repos.canvases.list(request.session.workspace_id)
        return Response.json(
            {"canvases": [_shape.row(row) for row in rows]}
        )

    def create(request: RequestContext) -> Response:
        """Save a canvas. A graph with a cycle is refused before it is stored."""
        request.session.require("project:manage")
        row = app.repos.canvases.create(
            request.session.workspace_id, request.session.user_id, request.json()
        )
        return Response.json({"canvas": _canvas(app, request.session.workspace_id, str(row["id"]))}, status=201)

    def show(request: RequestContext) -> Response:
        """One canvas, its graph and the order a run would use."""
        return Response.json(
            {"canvas": _canvas(app, request.session.workspace_id, request.params["id"])}
        )

    def update(request: RequestContext) -> Response:
        """Edit a canvas. The whole graph is re-validated, never patched blind.

        A caller that sends ``expected_revision`` gets optimistic concurrency: a
        canvas that moved underneath it is a 409 naming the current revision, not
        a silent overwrite of somebody else's work.
        """
        request.session.require("project:manage")
        body = dict(request.json())
        expected = body.pop("expected_revision", None)
        app.repos.canvases.update(
            request.session.workspace_id,
            request.params["id"],
            body,
            user_id=request.session.user_id,
            expected_revision=expected,
        )
        return Response.json(
            {"canvas": _canvas(app, request.session.workspace_id, request.params["id"])}
        )

    def destroy(request: RequestContext) -> Response:
        """Remove a canvas. Runs and the jobs they queued keep their history."""
        request.session.require("project:manage")
        result = app.repos.canvases.soft_delete(
            request.session.workspace_id, request.params["id"]
        )
        return Response.json(dict(result, ok=True))

    def start_run(request: RequestContext) -> Response:
        """Open a run and queue every node whose inputs already exist.

        Refuses a canvas with no generate node: a run that would do nothing is a
        button that lies about having started.
        """
        request.session.require("generation:create")
        run = app.canvas_runs.start(request.session, request.params["id"])
        return Response.json({"run": _shape.row(run)}, status=202)

    def runs(request: RequestContext) -> Response:
        """Recent runs of one canvas."""
        rows = app.repos.canvases.list_runs(
            request.session.workspace_id, request.params["id"], limit=20
        )
        return Response.json({"runs": _shape.rows(rows)})

    def show_run(request: RequestContext) -> Response:
        """One run. A read advances the run, because a poll is the only clock
        that survives a serverless freeze; the operation is idempotent."""
        run = app.canvas_runs.advance(
            request.session, request.params["id"], request.params["runId"]
        )
        return Response.json({"run": _shape.row(run)})

    router.add("GET", "/api/canvases", index)
    router.add("POST", "/api/canvases", create)
    router.add("GET", "/api/canvases/{id}", show)
    router.add("PATCH", "/api/canvases/{id}", update)
    router.add("DELETE", "/api/canvases/{id}", destroy)
    router.add("POST", "/api/canvases/{id}/runs", start_run)
    router.add("GET", "/api/canvases/{id}/runs", runs)
    router.add("GET", "/api/canvases/{id}/runs/{runId}", show_run)