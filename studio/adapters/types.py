"""Wire types shared by every adapter.

These are the objects that move between the job runner and a provider. They are
deliberately explicit about uncertainty: a provider that reports no percentage
produces a status with progress=None rather than a number this platform
invented, and a provider that reports no cost produces an estimate that says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..errors import ApiError, CAPABILITY_UNSUPPORTED


# ---------------------------------------------------------------------------
# Where an adapter sends its requests
# ---------------------------------------------------------------------------


@dataclass
class AdapterContext:
    """Everything an adapter needs to talk to one connection, and nothing else.

    The plaintext credential lives here for the duration of a single operation.
    It is never logged, never returned in a payload, and never stored on the
    adapter instance, so a long-lived adapter object cannot leak it later.
    """

    connection_id: str
    base_url: str
    credential: Optional[str] = None
    auth_scheme: str = "bearer"
    auth_header_name: str = "Authorization"
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)
    http: Any = None
    timeout_seconds: Optional[float] = None
    max_redirects: Optional[int] = None

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "AdapterContext(connection_id=%r, base_url=%r, credential=%s)" % (
            self.connection_id,
            self.base_url,
            "'[redacted]'" if self.credential else "None",
        )

    __str__ = __repr__

    def auth_headers(self) -> Dict[str, str]:
        """The credential, placed where this provider expects to find it.

        A provider with no credential configured gets no header at all, rather
        than an empty Authorization header that some servers read as a rejected
        attempt and others read as anonymous access.
        """
        if not self.credential:
            return {}
        scheme = (self.auth_scheme or "bearer").lower()
        if scheme == "none":
            return {}
        if scheme == "query":
            # Query-string credentials are the provider's design, not ours; they
            # are applied by the adapter when it builds the URL.
            return {}
        if scheme == "header":
            return {self.auth_header_name or "Authorization": self.credential}
        return {self.auth_header_name or "Authorization": "Bearer " + self.credential}

    def headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        out: Dict[str, str] = {}
        out.update({str(k): str(v) for k, v in (self.extra_headers or {}).items()})
        out.update(self.auth_headers())
        if extra:
            out.update({str(k): str(v) for k, v in extra.items()})
        return out

    def query_auth(self) -> Dict[str, str]:
        if (self.auth_scheme or "").lower() == "query" and self.credential:
            name = self.config.get("auth_query_param") or "api_key"
            return {str(name): self.credential}
        return {}


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityProfile:
    """What one model can actually do, as the platform will present it.

    Every field here drives UI. A mode absent from modes is a disabled button
    with a stated reason; an aspect ratio absent from aspect_ratios is not
    offered at all. Nothing is inferred from the modality.
    """

    modality: str
    modes: Tuple[str, ...] = ()
    aspect_ratios: Tuple[str, ...] = ()
    resolutions: Tuple[str, ...] = ()
    durations_seconds: Tuple[float, ...] = ()
    frame_rates: Tuple[float, ...] = ()
    reference_roles: Tuple[str, ...] = ()
    max_reference_images: int = 0
    max_outputs: int = 1
    supports_negative_prompt: bool = False
    supports_seed: bool = False
    supports_audio: bool = False
    supports_mask: bool = False
    native_camera_moves: Tuple[str, ...] = ()
    restrictions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    max_input_bytes: Optional[int] = None

    def supports(self, mode: str) -> bool:
        return mode in self.modes

    def camera_move_is_native(self, key: str) -> bool:
        return key in self.native_camera_moves

    def require_mode(self, mode: str, model_label: str = "This model") -> None:
        """Refuse a mode this model does not declare, before anything is sent."""
        if not self.supports(mode):
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "%s does not support %s." % (model_label, mode.replace("_", " ")),
                details={"mode": mode, "available": list(self.modes)},
                hint="Choose a model that lists this mode, or use a supported one.",
            )

    def require_modes(self, modes: Sequence[str], model_label: str) -> None:
        for mode in modes:
            self.require_mode(mode, model_label)

    def merged(self, other: "CapabilityProfile") -> "CapabilityProfile":
        """Union two profiles. Used when a stored profile refines a discovered one."""
        return CapabilityProfile(
            modality=other.modality or self.modality,
            modes=_union(self.modes, other.modes),
            aspect_ratios=_union(self.aspect_ratios, other.aspect_ratios),
            resolutions=_union(self.resolutions, other.resolutions),
            durations_seconds=_union(self.durations_seconds, other.durations_seconds),
            frame_rates=_union(self.frame_rates, other.frame_rates),
            reference_roles=_union(self.reference_roles, other.reference_roles),
            max_reference_images=max(self.max_reference_images, other.max_reference_images),
            max_outputs=max(self.max_outputs, other.max_outputs),
            supports_negative_prompt=(
                self.supports_negative_prompt or other.supports_negative_prompt
            ),
            supports_seed=self.supports_seed or other.supports_seed,
            supports_audio=self.supports_audio or other.supports_audio,
            supports_mask=self.supports_mask or other.supports_mask,
            native_camera_moves=_union(self.native_camera_moves, other.native_camera_moves),
            restrictions=other.restrictions or self.restrictions,
            notes=_union(self.notes, other.notes),
            max_input_bytes=other.max_input_bytes or self.max_input_bytes,
        )

    def to_public(self) -> Dict[str, Any]:
        return {
            "modality": self.modality,
            "modes": list(self.modes),
            "aspect_ratios": list(self.aspect_ratios),
            "resolutions": list(self.resolutions),
            "durations_seconds": list(self.durations_seconds),
            "frame_rates": list(self.frame_rates),
            "reference_roles": list(self.reference_roles),
            "max_reference_images": self.max_reference_images,
            "max_outputs": self.max_outputs,
            "supports_negative_prompt": self.supports_negative_prompt,
            "supports_seed": self.supports_seed,
            "supports_audio": self.supports_audio,
            "supports_mask": self.supports_mask,
            "native_camera_moves": list(self.native_camera_moves),
            "restrictions": list(self.restrictions),
            "notes": list(self.notes),
            "max_input_bytes": self.max_input_bytes,
        }

    @classmethod
    def from_public(cls, raw: Any) -> "CapabilityProfile":
        if not isinstance(raw, Mapping):
            return cls(modality="other")
        return cls(
            modality=str(raw.get("modality") or "other"),
            modes=_tuple_of_str(raw.get("modes")),
            aspect_ratios=_tuple_of_str(raw.get("aspect_ratios")),
            resolutions=_tuple_of_str(raw.get("resolutions")),
            durations_seconds=tuple(
                float(x) for x in _list(raw.get("durations_seconds")) if _is_number(x)
            ),
            frame_rates=tuple(float(x) for x in _list(raw.get("frame_rates")) if _is_number(x)),
            reference_roles=_tuple_of_str(raw.get("reference_roles")),
            max_reference_images=int(raw.get("max_reference_images") or 0),
            max_outputs=max(1, int(raw.get("max_outputs") or 1)),
            supports_negative_prompt=bool(raw.get("supports_negative_prompt")),
            supports_seed=bool(raw.get("supports_seed")),
            supports_audio=bool(raw.get("supports_audio")),
            supports_mask=bool(raw.get("supports_mask")),
            native_camera_moves=_tuple_of_str(raw.get("native_camera_moves")),
            restrictions=_tuple_of_str(raw.get("restrictions")),
            notes=_tuple_of_str(raw.get("notes")),
            max_input_bytes=(
                int(raw["max_input_bytes"]) if _is_number(raw.get("max_input_bytes")) else None
            ),
        )


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _tuple_of_str(value: Any) -> Tuple[str, ...]:
    return tuple(str(item) for item in _list(value) if item is not None and str(item))


def _union(left: Sequence[Any], right: Sequence[Any]) -> Tuple[Any, ...]:
    out: List[Any] = list(left)
    for item in right:
        if item not in out:
            out.append(item)
    return tuple(out)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Requests and jobs
# ---------------------------------------------------------------------------


@dataclass
class GenerationRequest:
    """One generation, fully resolved and validated, on its way to a provider."""

    model_key: str
    mode: str
    prompt: str = ""
    negative_prompt: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    references: Tuple[Dict[str, Any], ...] = ()
    mask_url: Optional[str] = None
    prompt_instructions: Tuple[str, ...] = ()
    seed: Optional[int] = None
    count: int = 1
    extras: Dict[str, Any] = field(default_factory=dict)

    def composed_prompt(self) -> str:
        """The prompt as the provider should read it, instructions included.

        Prompt-mapped controls are appended, not substituted: the user's own
        words stay first and intact, which is the whole point of keeping them
        separate from a parameter in the first place.
        """
        parts = [self.prompt.strip()] if self.prompt and self.prompt.strip() else []
        parts.extend(item for item in self.prompt_instructions if item)
        return ", ".join(parts)


@dataclass
class SubmittedJob:
    """What a provider gives back when a job is accepted."""

    provider_job_id: str
    status_url: Optional[str] = None
    result_url: Optional[str] = None
    outputs: Tuple["ProviderOutput", ...] = ()
    raw_status: str = "queued"
    poll_after_seconds: Optional[float] = None


@dataclass
class JobStatus:
    """A provider's own view of a job, translated into our vocabulary.

    state is one of the states in studio.jobs. progress is None whenever the
    provider did not report one, because a fabricated percentage is worse than
    no percentage: it looks like information and is not.
    """

    state: str
    progress: Optional[float] = None
    provider_status: Optional[str] = None
    message: Optional[str] = None
    queue_position: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    error_code: Optional[str] = None
    outputs: Tuple["ProviderOutput", ...] = ()
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderOutput:
    """One finished artefact, as the provider described it."""

    url: str
    content_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    file_name: Optional[str] = None
    seed: Optional[int] = None
    provider_asset_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CostEstimate:
    """An estimate, labelled as one.

    is_estimate is always True when this platform computed the number and False
    only when the provider itself returned a final figure. is_billable records
    whose account is charged, which is the distinction the product has to keep
    straight: a platform-managed model spends platform credit, and a model
    behind the user's own key spends theirs.
    """

    currency: str = ""
    amount: Optional[float] = None
    is_estimate: bool = True
    is_billable: bool = False
    unit: str = ""
    quantity: Optional[float] = None
    note: str = ""
    unknown_reason: str = ""

    def to_public(self) -> Dict[str, Any]:
        return {
            "currency": self.currency,
            "amount": self.amount,
            "is_estimate": self.is_estimate,
            "is_billable": self.is_billable,
            "unit": self.unit,
            "quantity": self.quantity,
            "note": self.note,
            "unknown_reason": self.unknown_reason,
            "display": self.display(),
        }

    def display(self) -> str:
        if self.amount is None:
            return "Cost unavailable"
        amount = "%g" % round(self.amount, 4)
        label = ("about " if self.is_estimate else "") + (
            ("%s %s" % (amount, self.currency)).strip()
        )
        return label


@dataclass
class ConnectionTestResult:
    """The outcome of a real, bounded call made while testing a connection."""

    outcome: str
    ok: bool
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    message: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    models_seen: Tuple[str, ...] = ()

    def to_public(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "detail": self.detail,
            "models_seen": list(self.models_seen),
        }


@dataclass
class DiscoveredModel:
    """A model the provider itself listed, before a human confirmed anything."""

    model_key: str
    display_name: str = ""
    modality: str = "other"
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> Dict[str, Any]:
        return {
            "model_key": self.model_key,
            "display_name": self.display_name or self.model_key,
            "modality": self.modality,
        }
