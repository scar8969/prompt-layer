"""Projects, assets, uploads and the content routes the studio renders from."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from ..errors import VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from . import _shape


def _decode_base64(value: Any) -> bytes:
    """Decode an upload body, refusing anything that is not valid base64.

    A request that claims to be an upload but carries malformed data is refused
    here rather than stored as a mystery file.
    """
    text = str(value or "")
    if not text:
        raise ApiError(VALIDATION_FAILED, "The upload is empty.", field="data_base64")
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(
            VALIDATION_FAILED,
            "The uploaded file is not valid base64.",
            field="data_base64",
        ) from None


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    # -- projects ----------------------------------------------------------

    def projects(request: RequestContext) -> Response:
        """Projects in this workspace, newest first."""
        rows = app.repos.projects.list(request.session.workspace_id)
        return Response.json({"projects": _shape.rows(rows)})

    def create_project(request: RequestContext) -> Response:
        """Create a project."""
        request.session.require("project:manage")
        project = app.repos.projects.create(
            request.session.workspace_id, request.session.user_id, request.json()
        )
        return Response.json({"project": _shape.row(project)}, status=201)

    def show_project(request: RequestContext) -> Response:
        """One project with the assets inside it."""
        project = app.repos.projects.get(request.session.workspace_id, request.params["id"])
        assets, _cursor = app.repos.assets.list(
            request.session.workspace_id, project_id=request.params["id"]
        )
        return Response.json(
            {"project": _shape.row(project), "assets": _shape.assets(assets)}
        )

    def update_project(request: RequestContext) -> Response:
        """Rename, favourite or archive a project."""
        request.session.require("project:manage")
        project = app.repos.projects.update(
            request.session.workspace_id, request.params["id"], request.json()
        )
        return Response.json({"project": _shape.row(project)})

    def delete_project(request: RequestContext) -> Response:
        """Remove a project. Its assets stay in the library, unassigned."""
        request.session.require("project:manage")
        result = app.repos.projects.soft_delete(request.session.workspace_id, request.params["id"])
        return Response.json(dict(result, ok=True))

    def attach(request: RequestContext) -> Response:
        """Add assets to a project, refusing any that belong to someone else."""
        request.session.require("project:manage")
        body = request.json()
        project = app.repos.projects.attach_assets(
            request.session.workspace_id,
            request.params["id"],
            body.get("asset_ids") or [],
        )
        return Response.json({"project": _shape.row(project)})

    # -- assets ------------------------------------------------------------

    def assets(request: RequestContext) -> Response:
        """A page of assets, filtered by project, kind, favourite state or text."""
        limit = request.arg("limit") or "60"
        try:
            size = max(1, min(int(limit), 200))
        except ValueError:
            raise ApiError(VALIDATION_FAILED, "The limit must be a whole number.", field="limit") from None
        rows, cursor = app.repos.assets.list(
            request.session.workspace_id,
            project_id=request.arg("project_id") or None,
            kind=request.arg("kind") or None,
            favorite=request.flag("favorite"),
            q=request.arg("q") or None,
            limit=size,
            cursor=request.arg("cursor") or None,
        )
        return Response.json(
            {
                "assets": [_asset(row) for row in rows],
                "next_cursor": cursor,
            }
        )

    def _asset(row: dict) -> dict:
        """Add the two content routes to an asset row so the client never builds them."""
        return _shape.asset(row)

    def upload(request: RequestContext) -> Response:
        """Store a file the user picked. The type is proved from the bytes."""
        body = request.json()
        data = _decode_base64(body.get("data_base64"))
        asset = app.assets.upload(
            request.session,
            data=data,
            filename=str(body.get("file_name") or "upload"),
            declared_type=str(body.get("content_type") or ""),
            project_id=str(body.get("project_id") or "") or None,
        )
        return Response.json({"asset": _asset(asset)}, status=201)

    def show_asset(request: RequestContext) -> Response:
        """One asset."""
        row = app.repos.assets.get(request.session.workspace_id, request.params["id"])
        return Response.json({"asset": _asset(row)})

    def update_asset(request: RequestContext) -> Response:
        """Rename, move or favourite an asset."""
        request.session.require("asset:write")
        row = app.repos.assets.update(request.session.workspace_id, request.params["id"], request.json())
        return Response.json({"asset": _asset(row)})

    def delete_asset(request: RequestContext) -> Response:
        """Remove an asset from the library. Its job record keeps the history."""
        request.session.require("asset:delete")
        app.repos.assets.soft_delete(request.session.workspace_id, request.params["id"])
        return Response.json({"ok": True})

    def content(request: RequestContext) -> Response:
        """The stored bytes, with the content type the signature proved."""
        data, content_type, _row = app.assets.content(request.session, request.params["id"])
        return Response.bytes(data, content_type=content_type)

    def thumb(request: RequestContext) -> Response:
        """The stored thumbnail, or the full image when none was generated."""
        found = app.assets.thumbnail(request.session, request.params["id"])
        if found is None:
            data, content_type, _row = app.assets.content(request.session, request.params["id"])
            return Response.bytes(data, content_type=content_type)
        data, content_type = found
        return Response.bytes(data, content_type=content_type)

    router.add("GET", "/api/projects", projects)
    router.add("POST", "/api/projects", create_project)
    router.add("GET", "/api/projects/{id}", show_project)
    router.add("PATCH", "/api/projects/{id}", update_project)
    router.add("DELETE", "/api/projects/{id}", delete_project)
    router.add("POST", "/api/projects/{id}/assets", attach)
    router.add("GET", "/api/assets", assets)
    router.add("POST", "/api/assets", upload)
    router.add("GET", "/api/assets/{id}", show_asset)
    router.add("PATCH", "/api/assets/{id}", update_asset)
    router.add("DELETE", "/api/assets/{id}", delete_asset)
    router.add("GET", "/api/assets/{id}/content", content)
    router.add("GET", "/api/assets/{id}/thumbnail", thumb)
