"""Turning a studio request into a queued job, and the operations derived from it.

A generation POST does three things and then returns: it resolves the model and
its capability profile, it validates the request against that profile, and it
writes a queued row. It does not contact the provider. That separation is what
lets the image studio answer in milliseconds while a model takes two minutes,
and it is what makes a browser closing mid-generation harmless.

The one thing this module must never do is queue a request the model cannot
serve. A disabled button is an interface; a job that fails ninety seconds later
because the mode was never supported is a cost the user paid for.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..adapters.base import MODE_LABELS, mode_label
from ..adapters.core import get_adapter
from ..adapters.types import CapabilityProfile, GenerationRequest
from ..errors import (
    CAPABILITY_UNSUPPORTED,
    CONFIGURATION_REQUIRED,
    NOT_FOUND,
    VALIDATION_FAILED,
    ApiError,
)
from ..logging_setup import get_logger
from ..util import json_dumps, json_loads, new_id, now_iso

LOG = get_logger(__name__)

#: Camera moves the platform can express. A move that the model does not support
#: natively becomes prompt wording, and the client labels it as such.
MAX_PROMPT_LENGTH = 8000
MAX_REFERENCE_COUNT = 12


@dataclass(frozen=True)
class ResolvedModel:
    """A model row plus the profile the request must be checked against."""

    model: Dict[str, Any]
    connection: Dict[str, Any]
    profile: CapabilityProfile
    adapter: Any


class GenerationService:
    """Queue a generation, and answer the questions the studios ask before one.

    Everything here is synchronous and cheap. Anything that talks to a provider
    belongs to the runner.
    """

    def __init__(self, repositories: Any, connections: Any, assets: Any, config: Any) -> None:
        self.repos = repositories
        self.connections = connections
        self.assets = assets
        self.config = config

    # -- resolving ------------------------------------------------------------

    def resolve(self, session: Any, payload: Mapping[str, Any]) -> ResolvedModel:
        """The model, its connection and its profile, or a refusal saying which is missing.

        A model can be named by id, or by connection and model key. Both routes
        end at the same checks: the connection is enabled, the model is enabled,
        and the profile is what the user declared rather than what the modality
        label implies.
        """
        model_id = str(payload.get("model_id") or "").strip()
        if model_id:
            model = self.repos.models.get(session.workspace_id, model_id)
        else:
            connection_id = str(payload.get("connection_id") or "").strip()
            model_key = str(payload.get("model_key") or "").strip()
            if not connection_id or not model_key:
                raise ApiError(
                    VALIDATION_FAILED,
                    "Choose a model before generating.",
                    field="model_id",
                    hint="Pick a model in the studio, or register one on a connection.",
                )
            found = self.repos.models.find_by_key(session.workspace_id, connection_id, model_key)
            if found is None:
                raise ApiError(
                    NOT_FOUND,
                    "That connection does not have a model with that identifier.",
                    field="model_key",
                    details={"connection_id": connection_id, "model_key": model_key},
                )
            model = found
        if not model.get("is_enabled"):
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This model is disabled in this workspace.",
                field="model_id",
                hint="Enable it on the Models screen, then try again.",
            )
        connection = self.repos.connections.get(
            session.workspace_id, str(model.get("connection_id") or "")
        )
        if not connection.get("is_enabled"):
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "The provider connection behind this model is disabled.",
                field="model_id",
                hint="Re-enable the connection on the API Connections screen.",
            )
        adapter = get_adapter(str(connection.get("adapter_type") or ""))
        capabilities = model.get("capabilities") or {}
        if isinstance(capabilities, str):
            capabilities = json_loads(capabilities, {})
        modes = model.get("modes") or []
        if isinstance(modes, str):
            modes = json_loads(modes, [])
        profile = CapabilityProfile.from_public(
            dict(
                capabilities,
                modality=str(model.get("modality") or "image"),
                modes=tuple(modes or ()),
            )
        )
        return ResolvedModel(model=model, connection=connection, profile=profile, adapter=adapter)

    # -- validation -----------------------------------------------------------

    def build_request(
        self,
        session: Any,
        resolved: ResolvedModel,
        payload: Mapping[str, Any],
        *,
        mode: str,
        modality: str,
    ) -> Tuple[GenerationRequest, Dict[str, Any]]:
        """Validate the request body against the model profile and the mode.

        Returns the request and the resolved parameter payload. Every parameter
        is checked against the model own schema, so a value the provider has no
        field for is refused here rather than silently dropped later.
        """
        prompt = str(payload.get("prompt") or "")
        if len(prompt) > MAX_PROMPT_LENGTH:
            raise ApiError(
                VALIDATION_FAILED,
                "The prompt is longer than this platform accepts.",
                field="prompt",
                details={"length": len(prompt), "limit": MAX_PROMPT_LENGTH},
            )
        profile = resolved.profile
        if profile.modes and mode not in profile.modes:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This model does not offer %s." % mode_label(mode),
                field="mode",
                details={"mode": mode, "supported": list(profile.modes)},
                hint="Pick a model that supports this mode, or choose another mode.",
            )
        if mode != "text_to_image" and mode != "text_to_video" and not prompt.strip():
            if not (payload.get("references") or payload.get("mask_asset_id")):
                raise ApiError(
                    VALIDATION_FAILED,
                    "This mode needs a prompt, a reference image, or both.",
                    field="prompt",
                )
        references = self._references(session, payload.get("references") or ())
        mask_url = ""
        mask_id = str(payload.get("mask_asset_id") or "").strip()
        if mask_id:
            if not profile.supports_mask and mode != "inpaint":
                raise ApiError(
                    CAPABILITY_UNSUPPORTED,
                    "This model does not accept a mask.",
                    field="mask_asset_id",
                )
            mask = self.repos.assets.get(session.workspace_id, mask_id)
            mask_url = str(mask.get("id") or "")
        schema = resolved.model.get("parameter_schema") or {}
        if isinstance(schema, str):
            schema = json_loads(schema, {})
        from ..adapters.base import ParameterSchema, parse_parameter_schema

        parsed = parse_parameter_schema(schema)
        raw_parameters = payload.get("parameters") or {}
        if isinstance(raw_parameters, str):
            raw_parameters = json_loads(raw_parameters, {})
        if not isinstance(raw_parameters, Mapping):
            raise ApiError(VALIDATION_FAILED, "Parameters must be an object.", field="parameters")
        try:
            parameters, prompt_values = parsed.validate(dict(raw_parameters))
        except ApiError as error:
            error.field = error.field or "parameters"
            raise
        instructions: List[str] = []
        for name, value in prompt_values.items():
            spec = parsed.get(name)
            label = spec.display_label if spec else name
            instructions.append("%s: %s" % (label, value))
        camera_move = str(payload.get("camera_move") or "").strip()
        if camera_move:
            from ..adapters.base import camera_move as lookup_move

            move = lookup_move(camera_move)
            if move is None:
                raise ApiError(
                    VALIDATION_FAILED,
                    "There is no camera move called %r." % camera_move,
                    field="camera_move",
                )
            if move.is_native and move.parameter and move.value:
                parameters.setdefault(move.parameter, move.value)
            else:
                instructions.append(move.phrase)
        seed = payload.get("seed")
        if seed is not None:
            if not profile.supports_seed:
                raise ApiError(
                    CAPABILITY_UNSUPPORTED,
                    "This model does not accept a seed.",
                    field="seed",
                )
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                raise ApiError(VALIDATION_FAILED, "The seed must be a whole number.", field="seed") from None
        count = payload.get("count")
        count = 1 if count is None else int(count)
        if count < 1:
            raise ApiError(VALIDATION_FAILED, "Ask for at least one output.", field="count")
        if profile.max_outputs and count > profile.max_outputs:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This model produces at most %d output(s) per request." % profile.max_outputs,
                field="count",
                details={"requested": count, "maximum": profile.max_outputs},
            )
        request = GenerationRequest(
            model_key=str(resolved.model.get("model_key") or ""),
            mode=mode,
            prompt=prompt,
            negative_prompt=str(payload.get("negative_prompt") or "") or None,
            parameters=parameters,
            references=tuple(references),
            mask_url=mask_url or None,
            prompt_instructions=tuple(instructions),
            seed=seed,
            count=count,
        )
        resolved.adapter.validate_input(request, profile)
        extras = {"parameters_resolved": parameters, "instructions": instructions}
        return request, extras
    def _references(self, session: Any, raw: Any) -> List[Dict[str, Any]]:
        """Resolve reference assets to what the adapter will send.

        Only assets in this workspace are accepted, and the role is kept with the
        asset so a later job can reuse it with the same meaning.
        """
        if raw in (None, "", [], ()):
            return []
        if not isinstance(raw, (list, tuple)):
            raise ApiError(VALIDATION_FAILED, "References must be a list.", field="references")
        if len(raw) > MAX_REFERENCE_COUNT:
            raise ApiError(
                VALIDATION_FAILED,
                "At most %d reference images may be attached." % MAX_REFERENCE_COUNT,
                field="references",
            )
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ApiError(VALIDATION_FAILED, "Each reference must be an object.", field="references")
            asset_id = str(item.get("asset_id") or "").strip()
            url = str(item.get("url") or "").strip()
            role = str(item.get("role") or "main_subject").strip().lower()
            if not asset_id and not url:
                raise ApiError(
                    VALIDATION_FAILED,
                    "Each reference needs an asset or a URL.",
                    field="references",
                )
            entry: Dict[str, Any] = {"role": role}
            if asset_id:
                asset = self.repos.assets.get(session.workspace_id, asset_id)
                entry["asset_id"] = asset_id
                entry["url"] = self._asset_url(asset)
                entry["content_type"] = asset.get("mime_type")
                # `kind` is what decides whether a mode that needs a source video
                # has one. It was never set here, so every such mode (extend,
                # video_to_video, restyle, motion_transfer) was refused with "this
                # mode needs a source video" -- about a reference that was attached.
                entry["kind"] = str(asset.get("kind") or "") or (
                    "video"
                    if str(asset.get("mime_type") or "").startswith("video/")
                    else "image"
                )
            else:
                entry["url"] = url
            out.append(entry)
        return out

    def _asset_url(self, asset: Mapping[str, Any]) -> str:
        """A URL an adapter can fetch for this asset.

        The platform serves asset content from its own route, so an adapter that
        needs a URL gets one on the public base instead of a filesystem path.
        """
        base = (self.config.api_base_url or self.config.public_app_url or "").rstrip("/")
        if not base:
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "This deployment has no public base URL, so a reference image cannot be given to a provider.",
                details={"missing": ["STUDIO_API_BASE_URL or STUDIO_PUBLIC_APP_URL"]},
                hint="Set one of those variables and restart.",
            )
        return "%s/api/assets/%s/content" % (base, asset.get("id"))

    # -- queueing --------------------------------------------------------------

    def queue(
        self,
        session: Any,
        payload: Mapping[str, Any],
        *,
        modality: str,
        mode: str = "",
    ) -> Dict[str, Any]:
        """Validate and enqueue one generation. Returns the job row, not its output.

        Duplicate-submission protection: a client_request_id is turned into an
        idempotency key scoped to the workspace, so a double-click or a retried
        request returns the job that already exists instead of buying a second
        one.
        """
        session.require("generation:create")
        resolved = self.resolve(session, payload)
        chosen_mode = (mode or str(payload.get("mode") or "")).strip().lower()
        if not chosen_mode:
            chosen_mode = "text_to_image" if modality == "image" else "text_to_video"
        request, extras = self.build_request(
            session, resolved, payload, mode=chosen_mode, modality=modality
        )
        client_key = str(payload.get("client_request_id") or "").strip()
        idempotency_key = ""
        if client_key:
            idempotency_key = hashlib.sha256(
                ("%s|%s|%s" % (session.workspace_id, modality, client_key)).encode("utf-8")
            ).hexdigest()[:40]
            existing = self.repos.jobs.find_by_idempotency(session.workspace_id, idempotency_key)
            if existing is not None:
                return {"job": existing, "deduplicated": True}
        project_id = str(payload.get("project_id") or "").strip() or None
        if project_id:
            self.repos.projects.get(session.workspace_id, project_id)
        estimate = None
        try:
            estimate = resolved.adapter.estimate_cost(
                self.connections.context_for(resolved.connection, with_credential=False),
                request,
                resolved.profile,
            )
        except ApiError:
            estimate = None
        snapshot = {
            "model_key": request.model_key,
            "mode": request.mode,
            "profile": resolved.profile.to_public(),
            "mask_url": request.mask_url or "",
            "prompt_instructions": list(request.prompt_instructions),
            "catalog": {
                "aspect_ratio": payload.get("aspect_ratio"),
                "resolution": payload.get("resolution"),
                "duration_seconds": payload.get("duration_seconds"),
                "camera_move": payload.get("camera_move"),
            },
            "extras": extras,
        }
        # The repository owns the column names and validates every id it is
        # given, so the payload here is the vocabulary it declared rather than a
        # guess at the schema.
        row: Dict[str, Any] = {
            "project_id": project_id,
            "connection_id": resolved.connection.get("id"),
            "model_id": resolved.model.get("id"),
            "modality": modality,
            "mode": request.mode,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt or "",
            "parameters": request.parameters,
            "request": snapshot,
            "references": list(request.references),
            "status": "queued",
            "max_attempts": self.config.jobs.max_attempts,
            "cost_estimate": estimate.to_public() if estimate is not None else {},
            "idempotency_key": idempotency_key or None,
        }
        job = self.repos.jobs.create(session.workspace_id, session.user_id, row)
        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "generation.queued",
            target_type="job",
            target_id=str(job.get("id") or ""),
            detail={
                "modality": modality,
                "mode": request.mode,
                "model_id": resolved.model.get("id"),
            },
        )
        return {"job": job, "deduplicated": False}

    # -- derived operations ------------------------------------------------------

    def _parent(self, session: Any, job_id: str) -> Dict[str, Any]:
        """The job a derived generation descends from, with its workspace check."""
        session.require("generation:read")
        return self.repos.jobs.get(session.workspace_id, job_id)

    def variations(self, session: Any, job_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Queue the same request again with a fresh seed.

        A variation is a new job, not an edit of the old one: the original output
        stays exactly as it was produced.
        """
        parent = self._parent(session, job_id)
        request = dict(json_loads(parent.get("request_json") or "{}", {}))
        merged = {
            "model_id": parent.get("model_id"),
            "mode": parent.get("mode"),
            "prompt": payload.get("prompt") or parent.get("prompt") or "",
            "negative_prompt": parent.get("negative_prompt") or "",
            "parameters": json_loads(parent.get("parameters_json") or "{}", {}),
            "references": json_loads(parent.get("references_json") or "[]", []),
            "count": payload.get("count") or parent.get("output_count") or 1,
            "seed": payload.get("seed"),
            "project_id": parent.get("project_id"),
        }
        result = self.queue(
            session,
            merged,
            modality=str(parent.get("modality") or "image"),
            mode=str(parent.get("mode") or ""),
        )
        job = result.get("job") or {}
        self.repos.jobs.link_parent(
            session.workspace_id, str(job.get("id") or ""), job_id, "variation"
        )
        job["parent_job_id"] = job_id
        job["relation"] = "variation"
        return result

    def _derived_references(
        self, parent: Mapping[str, Any], role: str
    ) -> List[Dict[str, Any]]:
        """The parent's own references, plus the output it produced.

        An edit, an extension or a transform is derived *from an output*. These
        routes used to copy only the parent's input references, so a request whose
        mode needs a source image or video was refused with "this mode needs a
        source video" — about a job that had just produced one. The output asset is
        appended with the role the derived request expects, and references are
        de-duplicated by asset id so a parent that already carried its own output
        is not doubled.
        """
        references = list(json_loads(parent.get("references_json") or "[]", []))
        seen = {str(item.get("asset_id") or "") for item in references if isinstance(item, Mapping)}
        for output in parent.get("outputs") or ():
            asset_id = str(output.get("asset_id") or "")
            if not asset_id or asset_id in seen:
                continue
            references.append({"asset_id": asset_id, "role": role})
            break
        return references

    def edit(self, session: Any, job_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Queue an inpaint or outpaint derived from an existing output.

        The mode decides what is required: inpainting needs a mask, outpainting
        needs an expansion. A request that claims a mode the model does not
        support is refused before it is queued.
        """
        parent = self._parent(session, job_id)
        mode = str(payload.get("mode") or "inpaint").strip().lower()
        if mode not in ("inpaint", "outpaint", "image_to_image"):
            raise ApiError(
                VALIDATION_FAILED,
                "An edit must be inpaint, outpaint or image_to_image.",
                field="mode",
            )
        parameters = dict(json_loads(parent.get("parameters_json") or "{}", {}))
        expand = payload.get("expand")
        if isinstance(expand, Mapping):
            for edge in ("left", "right", "top", "bottom"):
                if expand.get(edge) is not None:
                    parameters.setdefault("expand_%s" % edge, expand.get(edge))
        merged = {
            "model_id": payload.get("model_id") or parent.get("model_id"),
            "mode": mode,
            "prompt": payload.get("prompt") or parent.get("prompt") or "",
            "negative_prompt": parent.get("negative_prompt") or "",
            "parameters": dict(parameters, **(payload.get("parameters") or {})),
            "mask_asset_id": payload.get("mask_asset_id"),
            "references": self._derived_references(parent, "first_frame"),
            "count": 1,
            "project_id": parent.get("project_id"),
        }
        if mode == "inpaint" and not merged.get("mask_asset_id"):
            raise ApiError(
                VALIDATION_FAILED,
                "Inpainting needs a mask that marks the area to change.",
                field="mask_asset_id",
                hint="Paint the area in the studio, then submit the edit.",
            )
        result = self.queue(
            session, merged, modality=str(parent.get("modality") or "image"), mode=mode
        )
        job = result.get("job") or {}
        self.repos.jobs.link_parent(
            session.workspace_id, str(job.get("id") or ""), job_id, mode
        )
        return result

    def extend(self, session: Any, job_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Queue a video extension of an existing job output."""
        parent = self._parent(session, job_id)
        seconds = payload.get("seconds")
        if seconds is None:
            raise ApiError(
                VALIDATION_FAILED,
                "Say how many seconds to add.",
                field="seconds",
            )
        merged = {
            "model_id": payload.get("model_id") or parent.get("model_id"),
            "mode": "extend",
            "prompt": payload.get("prompt") or parent.get("prompt") or "",
            "parameters": dict(
                json_loads(parent.get("parameters_json") or "{}", {}),
                **(payload.get("parameters") or {}),
            ),
            "references": self._derived_references(parent, "reference_video"),
            "count": 1,
            "project_id": parent.get("project_id"),
            "duration_seconds": seconds,
        }
        result = self.queue(
            session, merged, modality=str(parent.get("modality") or "video"), mode="extend"
        )
        job = result.get("job") or {}
        self.repos.jobs.link_parent(
            session.workspace_id, str(job.get("id") or ""), job_id, "extension"
        )
        return result

    def transform(self, session: Any, job_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Queue a video-to-video transform of an existing job output."""
        parent = self._parent(session, job_id)
        mode = str(payload.get("mode") or "video_to_video").strip().lower()
        merged = {
            "model_id": payload.get("model_id") or parent.get("model_id"),
            "mode": mode,
            "prompt": payload.get("prompt") or parent.get("prompt") or "",
            "parameters": dict(
                json_loads(parent.get("parameters_json") or "{}", {}),
                **(payload.get("parameters") or {}),
            ),
            "references": self._derived_references(parent, "reference_video"),
            "count": 1,
            "project_id": parent.get("project_id"),
        }
        result = self.queue(
            session, merged, modality=str(parent.get("modality") or "video"), mode=mode
        )
        job = result.get("job") or {}
        self.repos.jobs.link_parent(
            session.workspace_id, str(job.get("id") or ""), job_id, "transform"
        )
        return result
