"""Turning a provider output into a stored asset the library can serve.

One path in, one shape out. Every generated artefact, whether it came back as a
link, as inline base64, or as a staging file the adapter already wrote, ends up
here, and the rules are the same for all three:

* the bytes are inspected before they are stored, so a renamed executable or an
  HTML error page cannot become a PNG in the user gallery;
* the object key is scoped to the workspace, so one workspace cannot construct
  another one key by guessing;
* the asset row is written only after the bytes are safely in object storage, so
  a failed upload leaves no row pointing at nothing.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Any, Dict, Mapping, Optional

from ..errors import (
    OUTPUT_DOWNLOAD_FAILED,
    STORAGE_FAILED,
    VALIDATION_FAILED,
    ApiError,
)
from ..logging_setup import get_logger
from ..media import MediaInfo, extension_for, inspect, thumbnail
from ..net import client_for
from ..redact import redact_url
from ..storage import build_key
from ..util import humanize_bytes, json_dumps, new_id, now_iso

LOG = get_logger(__name__)

#: What a provider said an output was, reduced to what we can act on.


def _kind_of(info: MediaInfo) -> str:
    """Map an inspected media type onto the asset kind vocabulary."""
    if info.kind in ("image", "video", "audio"):
        return info.kind
    return "other"


def download_output(
    config: Any,
    url: str,
    *,
    expected_content_type: Optional[str] = None,
    destination: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch a provider output to a temporary file, bounded by the egress policy.

    Refuses a redirect, an over-large body, an empty body, and a content type
    that contradicts what the provider said the output was. The file is staged
    under the deployment temporary directory and removed by the caller.
    """
    target = destination or os.path.join(
        tempfile.gettempdir(), "studio-download-" + new_id() + ".bin"
    )
    client = client_for(config)
    result = client.download(
        url,
        target,
        max_bytes=config.egress.max_download_bytes,
        expected_content_type=expected_content_type,
    )
    return {"path": target, "bytes": result.get("bytes", 0), "content_type": result.get("content_type", "")}


class AssetService:
    """Store generated and uploaded media for one workspace.

    The store is injected rather than constructed here, so a deployment with an
    S3-backed bucket and a deployment writing to local disk run the same code."
    """

    def __init__(self, repositories: Any, store: Any, config: Any) -> None:
        self.repos = repositories
        self.store = store
        self.config = config

    # -- storage primitives ---------------------------------------------------

    def key_for(self, workspace_id: str, kind: str, content_type: str) -> tuple:
        """A fresh object key and asset id for one new artefact."""
        asset_id = new_id()
        key = build_key(workspace_id, "%ss" % kind, asset_id, extension_for(content_type))
        return asset_id, key

    def store_bytes(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        data: bytes,
        declared_type: str = "",
        filename: str = "",
        project_id: Optional[str] = None,
        source: str = "generated",
        parent_asset_id: Optional[str] = None,
        reference_role: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Inspect, store and index one artefact. Returns the asset row.

        Refuses bytes that are not a media type this deployment accepts, bytes
        that contradict the declared type, and a body over the configured limit.
        The object is written before the row, so a row never points at nothing.
        """
        if not data:
            raise ApiError(VALIDATION_FAILED, "There is nothing to store: the file is empty.")
        info = inspect(
            data,
            declared_type=declared_type,
            max_image_bytes=self.config.media.max_upload_bytes,
            max_video_bytes=self.config.media.max_video_upload_bytes,
        )
        kind = _kind_of(info)
        asset_id, key = self.key_for(workspace_id, kind, info.content_type)
        try:
            stored = self.store.put(key, data, info.content_type)
        except ApiError:
            raise
        except OSError as error:
            raise ApiError(
                STORAGE_FAILED,
                "The file could not be written to object storage.",
                provider_message=str(error),
            ) from error
        thumb_key = ""
        if kind == "image":
            thumb_bytes, thumb_type = thumbnail(
                data, max_edge=self.config.media.thumbnail_max_pixels
            )
            if thumb_bytes:
                candidate = build_key(workspace_id, "thumbnails", asset_id, extension_for(thumb_type))
                try:
                    self.store.put(candidate, thumb_bytes, thumb_type)
                    thumb_key = candidate
                except Exception:  # noqa: BLE001 - a missing thumbnail is not a failed asset
                    thumb_key = ""
        row = {
            "id": asset_id,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "project_id": project_id,
            "kind": kind,
            "source": source if source in ("upload", "generated", "derived") else "generated",
            "storage_key": key,
            "thumbnail_key": thumb_key or None,
            "filename": (filename or os.path.basename(key))[:200],
            "mime_type": info.content_type,
            "byte_size": len(data),
            "checksum": hashlib.sha256(data).hexdigest(),
            "width": info.width or None,
            "height": info.height or None,
            "duration_seconds": info.duration_seconds or None,
            "reference_role": reference_role,
            "metadata_json": json_dumps(dict(metadata or {})),
            "parent_asset_id": parent_asset_id,
        }
        asset = self.repos.assets.create(workspace_id, row)
        return asset

    def ingest_output(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        output: Any,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import one ProviderOutput, from a link or from a staging file.

        A provider that returned a URL gets a bounded download. An adapter that
        already wrote the bytes locally (the mock provider, and a declarative
        mapping that answered inline) hands over a path instead. Both end at the
        same store_bytes call, so both get inspected the same way.
        """
        metadata = dict(getattr(output, "metadata", {}) or {})
        local_path = str(metadata.get("local_path") or "")
        declared = str(getattr(output, "content_type", "") or "")
        filename = str(getattr(output, "file_name", "") or "")
        staging: Optional[str] = None
        try:
            if local_path:
                if not os.path.isfile(local_path):
                    raise ApiError(
                        OUTPUT_DOWNLOAD_FAILED,
                        "The file this provider produced is no longer on disk.",
                        details={"path": local_path},
                    )
                with open(local_path, "rb") as handle:
                    data = handle.read()
            elif getattr(output, "url", ""):
                fetched = download_output(
                    self.config,
                    output.url,
                    expected_content_type=declared or None,
                )
                staging = fetched["path"]
                with open(staging, "rb") as handle:
                    data = handle.read()
                declared = declared or fetched.get("content_type", "")
            else:
                raise ApiError(
                    OUTPUT_DOWNLOAD_FAILED,
                    "The provider reported an output with neither a link nor stored bytes.",
                )
            asset = self.store_bytes(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                data=data,
                declared_type=declared,
                filename=filename,
                project_id=project_id,
                source="generated",
                metadata={"job_id": job_id, "provider_asset_id": getattr(output, "provider_asset_id", None)},
            )
            return asset
        finally:
            if staging:
                try:
                    os.remove(staging)
                except OSError:
                    pass

    def upload(
        self,
        session: Any,
        *,
        data: bytes,
        filename: str,
        declared_type: str = "",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store a file the user uploaded. Same inspection as a generated output."""
        session.require("asset:write")
        if project_id:
            self.repos.projects.get(session.workspace_id, project_id)
        return self.store_bytes(
            workspace_id=session.workspace_id,
            owner_user_id=session.user_id,
            data=data,
            declared_type=declared_type,
            filename=filename,
            project_id=project_id,
            source="upload",
        )

    # -- reading ---------------------------------------------------------------

    def content(self, session: Any, asset_id: str) -> tuple:
        """The stored bytes and their content type, for one asset in this workspace.

        Refuses an asset belonging to another workspace identically to one that
        does not exist, so an id cannot be probed.
        """
        session.require("asset:read")
        asset = self.repos.assets.get(session.workspace_id, asset_id)
        key = asset.get("storage_key") or ""
        if not key:
            raise ApiError(STORAGE_FAILED, "This asset has no stored file.")
        try:
            data = self.store.get(key)
        except ApiError:
            raise
        return data, str(asset.get("mime_type") or "application/octet-stream"), asset

    def thumbnail(self, session: Any, asset_id: str) -> Optional[tuple]:
        """The stored thumbnail, or None when none was produced."""
        session.require("asset:read")
        asset = self.repos.assets.get(session.workspace_id, asset_id)
        key = asset.get("thumbnail_key") or ""
        if not key:
            return None
        try:
            return self.store.get(key), "image/png"
        except ApiError:
            return None
