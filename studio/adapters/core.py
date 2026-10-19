"""The adapter interface, and the registry that resolves one by name.

An adapter is the only place in this codebase that knows a specific provider's
wire format. Everything above it -- the studios, the job runner, the model
registry -- speaks in the vocabulary of studio.adapters.types.

Two rules hold for every method here, and they are the reason the file is
mostly documentation:

1. A method either performs the real operation or raises. Returning a plausible
   invented value would be worse than failing, because the platform would then
   show a job that the provider never accepted.
2. An operation the provider does not offer raises UNSUPPORTED_OPERATION, not a
   generic error. The UI needs to distinguish "this provider cannot cancel" from
   "this cancel attempt failed".
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type

from ..errors import (
    ApiError,
    CAPABILITY_UNSUPPORTED,
    INTERNAL,
    INVALID_RESPONSE,
    UNSUPPORTED_OPERATION,
    from_http_status,
    provider_error,
)
from ..net import HttpClient
from .types import (
    AdapterContext,
    CapabilityProfile,
    ConnectionTestResult,
    CostEstimate,
    DiscoveredModel,
    GenerationRequest,
    JobStatus,
    ProviderOutput,
    SubmittedJob,
)

#: The states the job runner understands. Kept here as strings rather than an
#: Enum because they travel through the database and the API as plain text.
STATE_QUEUED = "queued"
STATE_PROCESSING = "processing"
STATE_DOWNLOADING = "downloading"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_TIMED_OUT = "timed_out"
ALL_STATES = (
    STATE_QUEUED,
    STATE_PROCESSING,
    STATE_DOWNLOADING,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_CANCELLED,
    STATE_TIMED_OUT,
)
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED, STATE_TIMED_OUT})


class ModelAdapter:
    """One provider protocol. Subclasses implement only what the provider offers."""

    #: Stable key stored in the database and used by the registry.
    slug: str = ""
    #: Shown in the connection wizard, e.g. "fal.ai queue API".
    label: str = ""
    #: One sentence for the wizard's type picker.
    summary: str = ""
    #: True only for the development adapter. Every surface that can show a
    #: model built on it reads this flag and labels it.
    is_mock: bool = False
    #: Credential schemes this adapter can write requests with.
    auth_schemes: Tuple[str, ...] = ("bearer", "header", "query", "none")
    #: True when the adapter can list the provider's models rather than requiring
    #: the user to type an identifier.
    discovers_models: bool = True
    #: True when the provider supports cancelling an in-flight job.
    supports_cancel: bool = False

    # -- capability declaration --------------------------------------------

    def default_modes(self) -> Tuple[str, ...]:
        """Modes assumed when a user registers a model by hand for this provider.

        Deliberately narrow. Guessing that a new image model supports inpainting
        because it is an image model is exactly the mistake the capability model
        exists to prevent, so this returns only the mode that every provider of
        this shape really implements.
        """
        return ()

    def default_capabilities(self, modality: str, modes: Sequence[str]) -> CapabilityProfile:
        return CapabilityProfile(modality=modality, modes=tuple(modes))

    # -- operations every adapter implements -------------------------------

    def test_connection(self, context: AdapterContext) -> ConnectionTestResult:
        """Make one real, bounded call and report what actually happened.

        Never returns ok=True unless the provider answered. The wizard shows
        this result verbatim, so an optimistic return here would be a lie shown
        to the user at the exact moment they are trusting us with a key.
        """
        raise NotImplementedError

    def list_models(self, context: AdapterContext) -> Tuple[DiscoveredModel, ...]:
        """List the provider's models, when the provider has such an endpoint."""
        raise ApiError(
            UNSUPPORTED_OPERATION,
            "This provider type does not publish a model list.",
            details={"adapter": self.slug},
            hint="Type the model identifier from the provider's documentation.",
        )

    def capabilities_for(
        self, context: AdapterContext, model_key: str
    ) -> Optional[CapabilityProfile]:
        """Ask the provider what this model supports, when it can be asked.

        Returning None means the provider publishes no capability metadata, and
        the platform will use what the user declared. That is honest; inventing
        a profile would not be.
        """
        return None

    def validate_input(self, request: GenerationRequest, profile: CapabilityProfile) -> None:
        """Refuse a request this model cannot serve, before it is queued.

        The base implementation checks the mode against the declared profile and
        the obvious input requirements. Adapters override to add provider rules.
        """
        profile.require_mode(request.mode, request.model_key)
        from .base import MODES_REQUIRING_IMAGE, MODES_REQUIRING_MASK, MODES_REQUIRING_VIDEO

        if request.mode in MODES_REQUIRING_IMAGE and not request.references:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This mode needs at least one source image.",
                details={"mode": request.mode},
                hint="Attach an image, or switch to text to image.",
            )
        if request.mode in MODES_REQUIRING_MASK and not request.mask_url:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "Inpainting needs a mask describing the area to change.",
                details={"mode": request.mode},
                hint="Paint a mask over the region you want the model to repaint.",
            )
        if request.mode in MODES_REQUIRING_VIDEO and not any(
            ref.get("kind") == "video" for ref in request.references
        ):
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This mode needs a source video.",
                details={"mode": request.mode},
                hint="Attach a video, or choose a mode that works from an image.",
            )
        if request.count > profile.max_outputs:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "This model produces at most %d output(s) per request."
                % profile.max_outputs,
                details={"requested": request.count, "maximum": profile.max_outputs},
            )

    def submit(self, context: AdapterContext, request: GenerationRequest) -> SubmittedJob:
        """Submit the generation. This is the one call that can cost money."""
        raise NotImplementedError

    def poll(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        """Ask the provider how the job is doing.

        Adapters for synchronous providers return a completed status with the
        outputs they already have, and never invent intermediate progress.
        """
        raise NotImplementedError

    def cancel(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        raise ApiError(
            UNSUPPORTED_OPERATION,
            "This provider does not support cancelling a submitted job.",
            details={"adapter": self.slug, "provider_job_id": job.provider_job_id},
            hint="The job will run to completion; you are not charged for cancelling.",
        )

    def estimate_cost(
        self, context: AdapterContext, request: GenerationRequest, profile: CapabilityProfile
    ) -> CostEstimate:
        """Return an estimate, or an estimate that says it does not know.

        The default answer is the honest one for a provider whose pricing this
        platform has no contract with: unknown, and billed to the user's own
        account rather than to platform credits.
        """
        return CostEstimate(
            is_estimate=True,
            is_billable=False,
            note="Billed by your provider on your own account.",
            unknown_reason="This provider does not publish per-request pricing through its API.",
        )

    def parse_error(self, error: ApiError) -> ApiError:
        """Translate a provider's failure into our vocabulary. Hook for overrides."""
        return error

    # -- helpers shared by subclasses --------------------------------------

    def _request(
        self,
        context: AdapterContext,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        body: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        accept: str = "application/json",
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        """One call through the SSRF-guarded client, with provider errors mapped.

        Pass json_body for a JSON request, or body for bytes that are already
        encoded (a multipart form, or a body the caller serialised itself). The
        credential travels in the headers built here and nowhere else, so a
        subclass never has to think about auth, and no log line can capture it.
        """
        client: HttpClient = context.http
        if client is None:
            raise ApiError(
                INTERNAL,
                "The adapter has no HTTP client.",
                details={"adapter": self.slug},
            )
        final = _with_query(url, params, context.query_auth())
        started = time.monotonic()
        try:
            response = client.request(
                method,
                final,
                headers=dict(context.headers(headers or {})),
                json_body=json_body,
                body=body,
                accept=accept,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else context.timeout_seconds,
            )
        except ApiError as error:
            raise self.parse_error(error) from None
        latency = int((time.monotonic() - started) * 1000)
        if response.status >= 400:
            raise self.parse_error(
                _error_from_response(response.status, response.body, self.label, latency)
            )
        return response

    def _json(
        self,
        context: AdapterContext,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        response = self._request(
            context,
            method,
            url,
            json_body=json_body,
            headers=headers,
            params=params,
            timeout_seconds=timeout_seconds,
        )
        return response.json()


def _with_query(
    url: str, params: Optional[Mapping[str, Any]], extra: Mapping[str, str]
) -> str:
    """Append query parameters without ever logging the credential-bearing ones."""
    from urllib.parse import urlencode

    pairs: List[Tuple[str, str]] = []
    for source in (params, extra):
        for key, value in (source or {}).items():
            if value is None or value is True and False:
                continue
            pairs.append((str(key), str(value)))
    if not pairs:
        return url
    separator = "&" if "?" in url else "?"
    return url + separator + urlencode(pairs)


def _error_from_response(
    status: int, body: bytes, provider: str, latency_ms: int
) -> ApiError:
    """Turn an HTTP failure into a coded error, keeping the provider's own words."""
    text = body[:2000].decode("utf-8", errors="replace").strip()
    message = ""
    try:
        import json as _json

        parsed = _json.loads(text) if text else None
        message = _extract_message(parsed)
    except Exception:
        message = text[:400]
    error = from_http_status(status, message or None)
    error.details = dict(error.details or {})
    error.details.setdefault("provider", provider)
    error.details.setdefault("latency_ms", latency_ms)
    return error


def _extract_message(parsed: Any) -> str:
    """Pull the human sentence out of the several shapes providers use."""
    if not isinstance(parsed, Mapping):
        return ""
    for key in ("message", "detail", "error", "msg", "reason", "description"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:400]
        if isinstance(value, Mapping):
            inner = value.get("message") or value.get("detail")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()[:400]
    return ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Type[ModelAdapter]] = {}


def register(adapter_cls: Type[ModelAdapter]) -> Type[ModelAdapter]:
    """Register an adapter class under its slug. Called at import time."""
    if not adapter_cls.slug:
        raise ValueError("An adapter must declare a slug")
    _REGISTRY[adapter_cls.slug] = adapter_cls
    return adapter_cls


def get_adapter(slug: str) -> ModelAdapter:
    """Return the singleton for a slug, or refuse with a usable message."""
    try:
        cls = _REGISTRY[slug]
    except KeyError:
        raise ApiError(
            CAPABILITY_UNSUPPORTED,
            "There is no adapter for provider type '%s'." % slug,
            details={"adapter_type": slug, "known": sorted(_REGISTRY)},
            hint="Re-run the connection wizard and pick a supported provider type.",
        ) from None
    return cls()


def adapter_types() -> List[Dict[str, Any]]:
    """The wizard's type picker, built from the registry so it cannot go stale."""
    out = []
    for slug in sorted(_REGISTRY):
        cls = _REGISTRY[slug]
        out.append(
            {
                "slug": cls.slug,
                "label": cls.label,
                "summary": cls.summary,
                "is_mock": cls.is_mock,
                "discovers_models": cls.discovers_models,
                "supports_cancel": cls.supports_cancel,
                "auth_schemes": list(cls.auth_schemes),
                "default_modes": list(cls().default_modes()),
            }
        )
    return out


def known_adapters() -> Tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def adapter_for_model(adapter_type: str) -> ModelAdapter:
    return get_adapter(adapter_type)
