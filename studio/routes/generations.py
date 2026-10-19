"""Generation submissions and the operations derived from an existing job."""

from __future__ import annotations

from typing import Any

from ..http import RequestContext, Response
from . import _shape


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def shaped(workspace_id: str, job: dict) -> dict:
        """A job with the provider facts, so the client can badge a mock."""
        return _shape.job_with_provider(app.repos, workspace_id, job)

    def queued(request: RequestContext, result: dict, status: int = 202) -> Response:
        """The one place a queued job becomes a response."""
        job = result["job"]
        workspace_id = str(job.get("workspace_id") or "")
        return Response.json(
            {
                "job": shaped(workspace_id, job),
                "deduplicated": bool(result.get("deduplicated")),
            },
            status=status,
        )

    def image(request: RequestContext) -> Response:
        """Queue one image generation. Returns the queued job, not its output."""
        result = app.generations.queue(request.session, request.json(), modality="image")
        return queued(request, result)

    def video(request: RequestContext) -> Response:
        """Queue one video generation."""
        result = app.generations.queue(request.session, request.json(), modality="video")
        return queued(request, result)

    def variations(request: RequestContext) -> Response:
        """Queue the same request again with a fresh seed."""
        result = app.generations.variations(request.session, request.params["id"], request.json())
        return queued(request, result)

    def edit(request: RequestContext) -> Response:
        """Queue an inpaint or outpaint derived from an existing job."""
        result = app.generations.edit(request.session, request.params["id"], request.json())
        return queued(request, result)

    def extend(request: RequestContext) -> Response:
        """Queue a video extension of an existing job output."""
        result = app.generations.extend(request.session, request.params["id"], request.json())
        return queued(request, result)

    def transform(request: RequestContext) -> Response:
        """Queue a video-to-video transform of an existing job output."""
        result = app.generations.transform(request.session, request.params["id"], request.json())
        return queued(request, result)

    router.add("POST", "/api/generations/images", image)
    router.add("POST", "/api/generations/videos", video)
    router.add("POST", "/api/generations/{id}/variations", variations)
    router.add("POST", "/api/generations/{id}/edit", edit)
    router.add("POST", "/api/generations/{id}/extend", extend)
    router.add("POST", "/api/generations/{id}/transform", transform)
