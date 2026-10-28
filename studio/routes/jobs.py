"""The job list, one job, its events, and the operations on a live job."""

from __future__ import annotations

from typing import Any

from ..errors import CONFLICT, VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def shape_job(workspace_id: str, job: dict) -> dict:
        """One job with the provider facts the client needs to badge a mock."""
        return _shape.job_with_provider(app.repos, workspace_id, job)

    def index(request: RequestContext) -> Response:
        """A page of jobs, newest first, with an opaque cursor for the next one."""
        limit = request.arg("limit") or "50"
        try:
            size = max(1, min(int(limit), 200))
        except ValueError:
            raise ApiError(
                VALIDATION_FAILED, "The limit must be a whole number.", field="limit"
            ) from None
        rows, cursor = app.repos.jobs.list(
            request.session.workspace_id,
            state=request.arg("state") or None,
            modality=request.arg("modality") or None,
            project_id=request.arg("project_id") or None,
            model_id=request.arg("model_id") or None,
            limit=size,
            cursor=request.arg("cursor") or None,
        )
        workspace_id = request.session.workspace_id
        return Response.json(
            {
                "jobs": [shape_job(workspace_id, item) for item in rows],
                "next_cursor": cursor,
            }
        )

    def show(request: RequestContext) -> Response:
        """One job with its events and outputs.

        If the job is still moving, this request also tries to advance it. On a
        deployment with no worker threads -- which is every serverless one -- the
        client's own poll is what makes progress, and the studio already polls
        this route while a job runs. `advance_inline` claims with SKIP LOCKED, so
        two polls arriving together cannot both drive the job.

        The drive is synchronous. A daemon thread was tried first and abandoned:
        measured two polls in three left the job untouched because the thread had
        not run before the response was written, and a serverless invocation can
        be frozen the moment it writes one. One step of work -- a submit, or a
        single poll -- is bounded by the adapter's own timeout, so blocking for it
        is cheaper than a mechanism that works three times in four.
        """
        from ..services.advance import advance_inline, should_advance

        workspace_id = request.session.workspace_id
        job = app.repos.jobs.get(workspace_id, request.params["id"])
        if should_advance(job):
            advance_inline(app, workspace_id, request.params["id"])
        return Response.json({"job": shape_job(workspace_id, job)})

    def events(request: RequestContext) -> Response:
        """The append-only history of what happened to this job."""
        app.repos.jobs.get(request.session.workspace_id, request.params["id"])
        rows = app.repos.jobs.events(
            request.params["id"], workspace_id=request.session.workspace_id
        )
        return Response.json({"events": _shape.rows(rows)})

    def cancel(request: RequestContext) -> Response:
        """Ask for a cancellation, and say honestly whether it happened yet.

        A job that has not reached the provider is cancelled outright. A job in
        flight gets a cancel request, and the worker reports the outcome when the
        provider confirms it -- this route never claims the provider stopped.
        """
        request.session.require("generation:cancel")
        job = app.repos.jobs.get(request.session.workspace_id, request.params["id"])
        if job.get("status") in ("completed", "cancelled", "failed", "timed_out"):
            raise ApiError(
                CONFLICT,
                "This job has already finished, so there is nothing to cancel.",
                details={"state": job.get("status")},
            )
        updated = app.repos.jobs.request_cancel(
            request.session.workspace_id, request.params["id"]
        )
        workspace_id = request.session.workspace_id
        return Response.json(
            {"job": shape_job(workspace_id, updated), "cancel_requested": True}
        )

    def retry(request: RequestContext) -> Response:
        """Queue a new job with the same request. The original row is untouched.

        A retry is a new row on purpose: reusing the old one would erase the record
        of the failure, and handing the old provider reference back to the worker
        would be a second billable call with nothing in the history to say so.
        """
        request.session.require("generation:create")
        job = app.repos.jobs.get(request.session.workspace_id, request.params["id"])
        if not app.repos.jobs.retryable(request.params["id"]):
            raise ApiError(
                CONFLICT,
                "This job cannot be retried.",
                details={
                    "state": job.get("status"),
                    "attempt": job.get("attempt"),
                    "max_attempts": job.get("max_attempts"),
                },
                hint="Submit a new generation, or retry a job that failed.",
            )
        payload = {
            "model_id": job.get("model_id"),
            "mode": job.get("mode"),
            "prompt": job.get("prompt") or "",
            "negative_prompt": job.get("negative_prompt") or "",
            "parameters": job.get("parameters") or {},
            "references": [
                {"asset_id": item.get("asset_id"), "role": item.get("role")}
                for item in (job.get("references") or [])
                if isinstance(item, dict) and item.get("asset_id")
            ],
            "project_id": job.get("project_id"),
            "count": 1,
        }
        result = app.generations.queue(
            request.session, payload, modality=str(job.get("modality") or "image")
        )
        new_job = result["job"]
        app.repos.jobs.link_parent(
            request.session.workspace_id,
            str(new_job.get("id")),
            request.params["id"],
            "retry",
        )
        workspace_id = request.session.workspace_id
        fresh = app.repos.jobs.get(workspace_id, str(new_job.get("id")))
        return Response.json(
            {"job": shape_job(workspace_id, fresh), "parent_job_id": request.params["id"]},
            status=202,
        )

    router.add("GET", "/api/jobs", index)
    router.add("GET", "/api/jobs/{id}", show)
    router.add("GET", "/api/jobs/{id}/events", events)
    router.add("POST", "/api/jobs/{id}/cancel", cancel)
    router.add("POST", "/api/jobs/{id}/retry", retry)
