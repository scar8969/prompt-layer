"""The fal.ai queue adapter: a real, asynchronous provider integration.

fal is a queue provider. A submit returns a request id in milliseconds; the
result is fetched later. That shape is why the platform's job model looks the
way it does -- a job is a row a worker advances, not one long HTTP call.

How this adapter knows what an endpoint accepts. It reads the provider's own
OpenAPI document at
    https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<endpoint-id>
That route is anonymous, so reading an endpoint's interface never sends a
user's key anywhere it is not needed. The hand-curated table in fal_endpoints.py
is only a fallback, and a profile built from it carries a note saying so, so a
reader can tell a live read from a stored copy.

Authentication. A fal credential travels as the literal header

    Authorization: Key <key-id>:<key-secret>

with no Bearer prefix, so this adapter writes that header itself instead of
relying on the generic scheme in AdapterContext. A stored value that already
begins with "Key " or "Bearer " is passed through untouched.

What this adapter refuses to do.
  * It never reports a progress percentage. fal reports IN_QUEUE and
    IN_PROGRESS and nothing in between, and an invented percentage is worse
    than no percentage because it looks like information.
  * It never reports a job as complete before the provider says COMPLETED.
  * It does not compute a price. fal publishes pricing as prose; the prose is
    quoted verbatim in a cost estimate whose amount stays unknown.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode

from ..errors import (
    ApiError,
    AUTH_FAILED,
    CAPABILITY_UNSUPPORTED,
    CONNECTION_TEST_OUTCOMES,
    ENDPOINT_NOT_FOUND,
    INTERNAL,
    INVALID_MODEL_ID,
    INVALID_RESPONSE,
    NETWORK_RESTRICTED,
    RATE_LIMITED,
    TIMEOUT,
    capability_unsupported,
    from_http_status,
    validation_error,
)
from . import mapping
from .core import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PROCESSING,
    STATE_QUEUED,
    STATE_TIMED_OUT,
    ModelAdapter,
    register,
)
from .fal_endpoints import endpoint as curated_endpoint
from .fal_schema import FalSchema, compile_curated, compile_schema, schema_url
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

#: The provider's queue host. A connection may point somewhere else; this is
#: only the default written into the connection wizard.
QUEUE_BASE = "https://queue.fal.run"

#: The public model catalog. It validates any credential it is given, which is
#: what makes it usable as a cheap key probe -- see verify_credential().
CATALOG_URL = "https://api.fal.ai/v1/models"

#: The public pricing listing. Every filter parameter this adapter tried is
#: ignored, and the listing paginates 40 records at a time, so one endpoint's
#: price is found by matching the listing's own "id" field. The answer is prose,
#: never a number, and it is repeated to the user verbatim.
PRICING_URL = "https://fal.ai/api/models"

#: How many pages of the pricing listing this platform will walk looking for one
#: endpoint. Bounded so that a miss costs a fixed handful of requests rather than
#: a crawl of the whole catalog.
PRICING_PAGE_LIMIT = 6

#: Hosts this adapter treats as fal itself.
FAL_HOSTS = ("queue.fal.run", "fal.run", "api.fal.ai", "api.fal.run")

#: A deliberately invalid credential. The connection test sends this to the
#: provider to prove that the provider checks credentials at all; a route that
#: answers the same way to this and to a real key has proved nothing.
SENTINEL_KEY = "00000000-0000-0000-0000-000000000000:00000000000000000000000000000000"

#: Used as the request id in a status probe. It is a valid UUID that no fal
#: account will ever have issued, so the probe can never touch a real job.
UNKNOWN_REQUEST_ID = "00000000-0000-0000-0000-000000000000"

CATALOG_PAGE_LIMIT = 100
MAX_CATALOG_PAGES = 5
SCHEMA_CACHE_LIMIT = 64
PRICING_CACHE_LIMIT = 64

#: fal's own status vocabulary, translated. Anything outside this table is
#: reported as unrecognised rather than guessed at.
STATUS_MAP = {
    "IN_QUEUE": STATE_QUEUED,
    "IN_PROGRESS": STATE_PROCESSING,
    "COMPLETED": STATE_COMPLETED,
    "OK": STATE_COMPLETED,
}
FAILED_STATUS_MAP = {
    "FAILED": STATE_FAILED,
    "ERROR": STATE_FAILED,
    "FAILURE": STATE_FAILED,
    "CANCELLED": STATE_CANCELLED,
    "CANCELED": STATE_CANCELLED,
    "CANCELLING": STATE_CANCELLED,
    "TIMEOUT": STATE_TIMED_OUT,
    "TIMED_OUT": STATE_TIMED_OUT,
}

#: Output containers to look in, in order. The first one that yields a usable
#: URL wins, so a payload carrying both a thumbnail list and a video is read as
#: the video.
OUTPUT_KEYS = ("images", "video", "videos", "output", "outputs", "result", "data", "image")

#: Payload keys that echo the request rather than describe the result. They are
#: skipped when falling back to a whole-body scan, so an input image URL cannot
#: be mistaken for an output.
ECHO_KEYS = frozenset({"payload", "input", "request", "logs", "prompt", "meta", "metadata"})

_SCHEMA_CACHE: Dict[str, FalSchema] = {}
_PRICING_CACHE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def clear_caches() -> None:
    """Drop the process-wide interface caches. Used by tests and by reload."""
    _SCHEMA_CACHE.clear()
    _PRICING_CACHE.clear()


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def is_fal_host(url: str) -> bool:
    host = _host_of(url)
    if not host:
        return True
    return host in FAL_HOSTS or host.endswith(".fal.run") or host.endswith(".fal.ai")


def authorization_value(credential: str) -> str:
    """The header value fal expects for this credential.

    Plain concatenation, not interpretation: a value that already carries a
    scheme is left alone, and a bare key gets fal's own "Key " prefix. The
    value is never logged and never returned to a client.
    """
    value = (credential or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith("key ") or lowered.startswith("bearer "):
        return value
    return "Key " + value


def _headers_for(context: AdapterContext, credential: Optional[str]) -> Dict[str, str]:
    """Headers for one call to a known fal route.

    Extra headers from the connection are honoured; the credential is written
    here so that a connection whose generic auth scheme was misconfigured still
    talks to fal correctly.
    """
    headers = dict(getattr(context, "extra_headers", None) or {})
    value = authorization_value(credential or "")
    if value:
        headers["Authorization"] = value
    return headers


def _fetch(
    context: AdapterContext,
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json_body: Any = None,
) -> Any:
    """One call that returns the response even when it is an error status.

    The connection test has to read 401 and 404 bodies to say anything useful,
    so this deliberately does not raise on HTTP status. Network-level failures
    still raise, because there is no response to inspect.
    """
    client = context.http
    if client is None:
        raise ApiError(INTERNAL, "The adapter has no HTTP client.", details={"adapter": "fal_queue"})
    final = url
    if params:
        pairs = [(str(k), str(v)) for k, v in params.items() if v is not None]
        if pairs:
            final = url + ("&" if "?" in url else "?") + urlencode(pairs)
    return client.request(
        method,
        final,
        headers=dict(headers or {}),
        json_body=json_body,
        accept="application/json",
        timeout_seconds=context.timeout_seconds,
    )


def _remember(cache: Dict[str, Any], key: str, value: Any, limit: int) -> None:
    if len(cache) >= limit:
        for stale in list(cache)[: max(1, limit // 4)]:
            cache.pop(stale, None)
    cache[key] = value


def _provider_message(response: Any) -> str:
    """Read the provider's own words out of an error body, if it sent any."""
    try:
        body = response.body
    except AttributeError:
        return ""
    text = body[:1000].decode("utf-8", errors="replace").strip() if body else ""
    if not text:
        return ""
    parsed = None
    try:
        import json as _json

        parsed = _json.loads(text)
    except ValueError:
        return text[:300]
    message = mapping.first_string(parsed, mapping.MESSAGE_KEYS) if isinstance(parsed, Mapping) else ""
    return (message or text)[:300]


# ---------------------------------------------------------------------------
# Interface reading
# ---------------------------------------------------------------------------


def _has_field(schema: Any, name: str) -> bool:
    return getattr(schema, "field", None) is not None and schema.field(name) is not None


def _field_for_role(schema: FalSchema, role: str) -> Optional[Any]:
    """The endpoint field that carries an image in the given role.

    Roles are matched against what the provider's schema actually declares.
    Nothing is inferred from the model's category, so a feature the schema does
    not mention has no field here and the caller is told so.
    """
    clean = (role or "reference").strip().lower()
    if clean == "first_frame":
        start = schema.start_frame_field()
        if start is not None:
            return start
    if clean == "mask":
        return schema.first_field_with_role("mask")
    if clean == "last_frame":
        return schema.first_field_with_role("last_frame")
    if clean in ("reference_video", "video", "motion_transfer"):
        return schema.first_field_with_role("reference_video") or schema.first_field_with_role("video")
    if clean == "audio":
        return schema.first_field_with_role("audio")
    return schema.first_field_with_role("reference") or schema.first_field_with_role("image")


def _field_is_list(field: Any) -> bool:
    types = tuple(getattr(field, "json_types", ()) or ())
    if "array" in types:
        return True
    primary = getattr(field, "primary_type", None)
    if callable(primary):
        return primary() == "array"
    return False


def _assign(body: Dict[str, Any], field: Any, url: str) -> None:
    """Put one URL into the endpoint field that carries it."""
    key = getattr(field, "name", None)
    if not key:
        raise ApiError(INTERNAL, "A provider field had no name.", details={"field": repr(field)})
    if _field_is_list(field):
        current = body.get(key)
        if current is None:
            current = []
            body[key] = current
        if not isinstance(current, list):
            raise validation_error(
                "This endpoint expects '" + key + "' to be a list, but something else was already set for it.",
                field="references",
            )
        if len(current) >= 20:
            raise validation_error("This endpoint accepts at most 20 images in '" + key + "'.", field="references")
        if url not in current:
            current.append(url)
        return
    existing = body.get(key)
    if existing is not None and existing != url:
        raise validation_error(
            "This endpoint has one field ('" + key + "') for two different images, so both cannot be sent. "
            "Remove one of the references.",
            field="references",
        )
    body[key] = url


def _count_field(schema: FalSchema) -> Optional[Any]:
    for name in ("num_images", "num_outputs", "number_of_images", "count", "n", "num_frames"):
        found = schema.field(name)
        if found is not None and ("integer" in tuple(getattr(found, "json_types", ()) or ())):
            return found
    return None


def build_fal_payload(request: GenerationRequest, schema: Optional[FalSchema] = None) -> Dict[str, Any]:
    """Translate a validated request into the endpoint's own field names.

    Parameters arrive already keyed by provider field name, because the model's
    ParameterSchema stores each control with its provider key. Everything else
    -- the prompt, the negative prompt, references and the mask -- is placed by
    reading the endpoint's schema, and anything the schema cannot place is
    refused with a message rather than dropped.
    """
    body: Dict[str, Any] = dict(request.parameters or {})
    prompt = (request.prompt or "").strip()
    negative = (request.negative_prompt or "").strip()
    instructions: List[str] = [item for item in (request.prompt_instructions or ()) if item]

    has_negative_field = schema is not None and _has_field(schema, "negative_prompt")
    if negative and not has_negative_field:
        # This endpoint cannot express a negative prompt, so it becomes prompt
        # wording. That is visible in the composed prompt rather than silent.
        instructions.append("avoid " + negative)

    composed = ", ".join([part for part in [prompt] + instructions if part])
    if composed:
        if schema is None:
            body.setdefault("prompt", composed)
        else:
            prompt_name = getattr(getattr(schema, "prompt_field", None), "name", None)
            if prompt_name:
                body[prompt_name] = composed
            else:
                raise validation_error(
                    "This endpoint takes no prompt text, so it cannot run a request that has one.",
                    field="prompt",
                    details={"model_key": request.model_key},
                )
    if negative and has_negative_field:
        body["negative_prompt"] = negative

    if request.seed is not None and schema is not None and _has_field(schema, "seed"):
        body.setdefault("seed", int(request.seed))

    count = int(request.count or 1)
    if count > 1:
        if schema is None:
            body.setdefault("num_images", count)
        else:
            target = _count_field(schema)
            if target is None:
                raise validation_error(
                    "This endpoint publishes no control for multiple outputs, so ask for one at a time.",
                    field="count",
                    details={"model_key": request.model_key, "requested": count},
                )
            body.setdefault(target.name, count)

    if request.references:
        if schema is None:
            raise validation_error(
                "This endpoint's interface could not be read, so this platform will not guess which field carries the reference images.",
                field="references",
                details={"model_key": request.model_key},
            )
        for entry in request.references:
            if not isinstance(entry, Mapping):
                raise validation_error(
                    "A reference must be an object with a 'role' and a 'url'.", field="references"
                )
            role = str(entry.get("role") or "reference")
            url = str(entry.get("url") or "").strip()
            if not url.startswith("http://") and not url.startswith("https://"):
                raise validation_error(
                    "A reference needs an absolute http(s) URL. The " + role.replace("_", " ") + " reference has none.",
                    field="references",
                )
            target = _field_for_role(schema, role)
            if target is None:
                raise capability_unsupported(
                    "This endpoint has no field for a " + role.replace("_", " ") + " image.",
                    details={"model_key": request.model_key, "role": role},
                )
            _assign(body, target, url)

    if request.mask_url:
        if schema is None:
            raise validation_error(
                "This endpoint's interface could not be read, so this platform will not guess which field carries the mask.",
                field="mask",
                details={"model_key": request.model_key},
            )
        target = schema.first_field_with_role("mask")
        if target is None:
            raise capability_unsupported(
                "This endpoint accepts no mask, so this edit cannot be sent to it.",
                details={"model_key": request.model_key, "mode": request.mode},
            )
        _assign(body, target, request.mask_url)

    return body


def _collect_outputs(value: Any, depth: int = 0) -> List[ProviderOutput]:
    """Read output records out of a provider payload.

    Only absolute http(s) URLs count as outputs, and the walk is depth-capped so
    it cannot wander through an unrelated structure. Metadata is carried across
    only when the provider actually labelled it.
    """
    found: List[ProviderOutput] = []
    if depth > 4:
        return found
    if isinstance(value, Mapping):
        url = mapping.first_string(value, mapping.URL_KEYS)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            found.append(
                ProviderOutput(
                    url=url,
                    content_type=_text(value.get("content_type") or value.get("mime_type")),
                    width=mapping.to_int(value.get("width")),
                    height=mapping.to_int(value.get("height")),
                    duration_seconds=mapping.to_float(
                        value.get("duration_seconds", value.get("duration"))
                    ),
                    file_name=_text(value.get("file_name") or value.get("filename")),
                    seed=mapping.to_int(value.get("seed")),
                )
            )
            return found
        for key, item in value.items():
            if str(key) in ECHO_KEYS:
                continue
            found.extend(_collect_outputs(item, depth + 1))
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_collect_outputs(item, depth + 1))
    return found


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def result_outputs(payload: Any) -> Tuple[ProviderOutput, ...]:
    """The outputs in a result payload, deduplicated, in provider order."""
    if not isinstance(payload, Mapping):
        return ()
    collected: List[ProviderOutput] = []
    for key in OUTPUT_KEYS:
        if key in payload:
            collected.extend(_collect_outputs(payload[key]))
        if collected:
            break
    if not collected:
        collected = _collect_outputs(payload)
    seen: List[str] = []
    out: List[ProviderOutput] = []
    for item in collected:
        if item.url in seen:
            continue
        seen.append(item.url)
        out.append(item)
    return tuple(out)


def translate_status(raw_status: str, payload: Any = None) -> str:
    """fal's status word in this platform's vocabulary.

    An unrecognised word is reported as processing, not as success, because the
    cost of waiting one more poll is seconds and the cost of showing a finished
    job that is not finished is a broken promise.
    """
    word = (raw_status or "").strip().upper()
    if word in FAILED_STATUS_MAP:
        return FAILED_STATUS_MAP[word]
    if word in STATUS_MAP:
        return STATUS_MAP[word]
    if isinstance(payload, Mapping) and mapping.first_present(payload, mapping.ERROR_KEYS):
        return STATE_FAILED
    if word:
        return STATE_PROCESSING
    return STATE_PROCESSING


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


@register
class FalQueueAdapter(ModelAdapter):
    slug = "fal_queue"
    label = "fal.ai queue API"
    summary = "fal.ai's asynchronous queue: submit a request, poll it, fetch the result."
    is_mock = False
    auth_schemes = ("header", "none")
    discovers_models = True
    supports_cancel = True

    # Read by the connection wizard and the model registry.
    credential_label = "fal API key"
    credential_hint = (
        "Paste the key from https://fal.ai/dashboard/keys . It looks like "
        "'<key-id>:<key-secret>'; the 'Key ' prefix is added for you if you leave it off."
    )
    base_url_hint = "Leave as-is unless you run your own fal-compatible queue."
    default_base_url = QUEUE_BASE
    docs_url = "https://fal.ai/models"

    def default_modes(self) -> Tuple[str, ...]:
        return ()

    # -- discovery ---------------------------------------------------------

    def test_connection(self, context: AdapterContext) -> ConnectionTestResult:
        """Verify the credential against a real, side-effect-free fal route.

        One GET cannot distinguish "this key is accepted" from "this route
        ignores keys", so the test also sends a deliberately invalid key. The
        connection is only reported as working when the provider rejected the
        invalid key and did not reject this one. Anything less would be a green
        tick shown at the exact moment the user is trusting us with a secret.
        """
        credential = (context.credential or "").strip()
        if not credential:
            return ConnectionTestResult(
                outcome=AUTH_FAILED,
                ok=False,
                message="No API key was entered for this connection.",
                detail={"field": "credential", "hint": self.credential_hint},
            )

        probe_url, model_label = self._probe_target(context)
        started = time.monotonic()
        try:
            good = _fetch(context, "GET", probe_url, headers=_headers_for(context, credential), params=self._probe_params())
        except ApiError as error:
            return self._failed_test(error, probe_url)

        latency = int((time.monotonic() - started) * 1000)
        good_status = int(good.status)
        good_message = _provider_message(good)

        if good_status in (401, 403):
            return ConnectionTestResult(
                outcome=AUTH_FAILED,
                ok=False,
                http_status=good_status,
                latency_ms=latency,
                message="fal rejected this API key.",
                detail={"field": "credential", "provider_message": good_message, "probe": probe_url},
            )
        if good_status == 429:
            return ConnectionTestResult(
                outcome=RATE_LIMITED,
                ok=False,
                http_status=good_status,
                latency_ms=latency,
                message="fal is rate limiting this connection right now.",
                detail={"probe": probe_url},
            )
        if good_status >= 500:
            return ConnectionTestResult(
                outcome="provider_error",
                ok=False,
                http_status=good_status,
                latency_ms=latency,
                message="fal answered with a server error.",
                detail={"probe": probe_url, "provider_message": good_message},
            )
        if good_status >= 400:
            error = from_http_status(good_status, good_message or None)
            return ConnectionTestResult(
                outcome=error.code if error.code in CONNECTION_TEST_OUTCOMES else INVALID_RESPONSE,
                ok=False,
                http_status=good_status,
                latency_ms=latency,
                message=error.message,
                detail={"probe": probe_url, "provider_message": good_message},
            )

        try:
            bad = _fetch(context, "GET", probe_url, headers=_headers_for(context, SENTINEL_KEY), params=self._probe_params())
        except ApiError as error:
            return self._failed_test(error, probe_url)
        bad_status = int(bad.status)
        if bad_status not in (401, 403):
            return ConnectionTestResult(
                outcome=AUTH_FAILED,
                ok=False,
                http_status=bad_status,
                latency_ms=latency,
                message=(
                    "This platform could not confirm the key: the endpoint answered the same way (HTTP "
                    + str(bad_status)
                    + ") to an invalid key as to yours, so it does not check credentials on the route used for testing."
                ),
                detail={
                    "probe": probe_url,
                    "verification": "sentinel-accepted",
                    "hint": (
                        "Register the model anyway and run one generation: submit is authenticated and "
                        "will say plainly if the key is wrong."
                    ),
                },
            )

        models_seen: Tuple[str, ...] = ()
        detail: Dict[str, Any] = {
            "probe": probe_url,
            "verification": "sentinel-rejected",
            "key_id": credential.split(":")[0] if ":" in credential else "",
        }
        if model_label:
            detail["endpoint"] = model_label
        if good_status == 200:
            models_seen = self._models_seen(good)
            if models_seen:
                detail["catalog_entries"] = len(models_seen)
        return ConnectionTestResult(
            outcome="",
            ok=True,
            http_status=good_status,
            latency_ms=latency,
            message=(
                "fal accepted this key. An invalid key sent to the same route was rejected, so the key "
                "is being read, not ignored."
            ),
            detail=detail,
            models_seen=models_seen,
        )

    def list_models(self, context: AdapterContext) -> Tuple[DiscoveredModel, ...]:
        """Page through fal's public model catalog.

        The catalog is the only list fal publishes; endpoint-level capability
        details still come from each endpoint's own schema when a model is
        registered.
        """
        out: List[DiscoveredModel] = []
        seen: List[str] = []
        cursor: Optional[str] = None
        params: Dict[str, Any] = {"limit": CATALOG_PAGE_LIMIT}
        for _page in range(MAX_CATALOG_PAGES):
            if cursor:
                params["cursor"] = cursor
            response = _fetch(
                context,
                "GET",
                self._catalog_url(context),
                headers=_headers_for(context, context.credential),
                params=params,
            )
            if response.status >= 400:
                raise from_http_status(response.status, _provider_message(response) or None)
            payload = response.json()
            items = payload.get("models") if isinstance(payload, Mapping) else None
            if not isinstance(items, list):
                raise ApiError(
                    INVALID_RESPONSE,
                    "The catalog answered with a shape this platform does not recognise.",
                    details={"keys": sorted(payload.keys())[:12] if isinstance(payload, Mapping) else type(payload).__name__},
                )
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                key = str(item.get("endpoint_id") or "").strip()
                if not key or key in seen:
                    continue
                seen.append(key)
                meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                category = str(meta.get("category") or "")
                out.append(
                    DiscoveredModel(
                        model_key=key,
                        display_name=str(meta.get("display_name") or key),
                        modality=modality_for_category(category),
                        raw={
                            "category": category,
                            "status": str(meta.get("status") or ""),
                            "group": str((meta.get("group") or {}).get("label") if isinstance(meta.get("group"), Mapping) else ""),
                        },
                    )
                )
            next_cursor = payload.get("next_cursor")
            if not payload.get("has_more") or not next_cursor:
                break
            cursor = str(next_cursor)
        return tuple(out)

    def capabilities_for(self, context: AdapterContext, model_key: str) -> Optional[CapabilityProfile]:
        """What the endpoint's own schema says it supports.

        Returns None when the schema cannot be reached, which tells the caller
        to use what the user declared rather than inventing a profile. An
        endpoint fal does not have is a hard error, not a missing profile.
        """
        try:
            return self.read_interface(context, model_key).capability_profile()
        except ApiError as error:
            if error.code in (INVALID_MODEL_ID, ENDPOINT_NOT_FOUND):
                raise
            if error.code in (NETWORK_RESTRICTED, TIMEOUT):
                return None
            raise

    def parameter_schema_for(self, context: AdapterContext, model_key: str):
        """The endpoint's own controls, read from its schema."""
        try:
            return self.read_interface(context, model_key).parameter_schema()
        except ApiError as error:
            if error.code in (INVALID_MODEL_ID, ENDPOINT_NOT_FOUND):
                raise
            return None

    def read_interface(self, context: AdapterContext, model_key: str) -> FalSchema:
        """The endpoint's interface, from its live schema or the curated copy."""
        return load_interface(context, model_key)

    # -- generation --------------------------------------------------------

    def submit(self, context: AdapterContext, request: GenerationRequest) -> SubmittedJob:
        """POST the request. This is the one call that can cost money.

        The interface is read first so the payload uses the endpoint's own field
        names. A request whose references cannot be placed is refused here, one
        step before the billable call.
        """
        schema: Optional[FalSchema] = None
        if request.references or request.mask_url:
            schema = load_interface(context, request.model_key)
        else:
            try:
                schema = load_interface(context, request.model_key)
            except ApiError:
                schema = None
        body = build_fal_payload(request, schema)
        paths = queue_paths(context, request.model_key)
        response = _fetch(
            context,
            "POST",
            paths["submit"],
            headers=_headers_for(context, context.credential),
            json_body=body,
        )
        if response.status >= 400:
            raise from_http_status(response.status, _provider_message(response) or None)
        payload = response.json()
        request_id = mapping.first_string(payload, mapping.ID_KEYS) if isinstance(payload, Mapping) else ""
        if not request_id:
            raise ApiError(
                INVALID_RESPONSE,
                "fal accepted the request but returned no request id, so this job cannot be tracked.",
                details={"keys": sorted(payload.keys())[:12] if isinstance(payload, Mapping) else ""},
                hint="Check the endpoint's schema: this adapter expects fal's queue contract.",
            )
        status_url = mapping.first_string(payload, ("status_url",)) or None
        result_url = (
            mapping.first_string(payload, ("response_url", "result_url", "output_url")) or None
        )
        raw_status = mapping.first_string(payload, mapping.STATUS_KEYS) or "IN_QUEUE"
        submitted = SubmittedJob(
            provider_job_id=request_id,
            status_url=status_url if _absolute(status_url) else paths["status"].replace("{request_id}", request_id),
            result_url=result_url if _absolute(result_url) else paths["result"].replace("{request_id}", request_id),
            outputs=result_outputs(payload),
            raw_status=raw_status,
        )
        # The job runner keeps the model key with the job so a poll can rebuild
        # the status route without a database lookup.
        submitted.model_key = request.model_key  # type: ignore[attr-defined]
        return submitted

    def poll(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        """Ask fal how the job is doing, and fetch the result once it is done."""
        status_url = job.status_url or self._rebuild(context, job, "status")
        response = _fetch(context, "GET", status_url, headers=_headers_for(context, context.credential))
        if response.status in (401, 403):
            raise ApiError(AUTH_FAILED, "fal rejected the key on this job's status route.")
        if response.status == 404:
            raise ApiError(
                ENDPOINT_NOT_FOUND,
                "fal does not know this job, so it cannot be tracked any further.",
                details={"provider_job_id": job.provider_job_id, "url": status_url},
                hint="Submit again, or check the endpoint identifier on the model.",
            )
        if response.status >= 400:
            raise from_http_status(response.status, _provider_message(response) or None)
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ApiError(
                INVALID_RESPONSE,
                "fal's status route answered with something other than a job object.",
                details={"provider_job_id": job.provider_job_id},
            )
        raw_status = mapping.first_string(payload, mapping.STATUS_KEYS)
        state = translate_status(raw_status, payload)
        position = mapping.to_int(mapping.first_present(payload, mapping.QUEUE_KEYS))
        message = mapping.first_string(payload, mapping.MESSAGE_KEYS) or None

        if state == STATE_COMPLETED:
            result_url = job.result_url or self._rebuild(context, job, "result")
            result = _fetch(context, "GET", result_url, headers=_headers_for(context, context.credential))
            if result.status in (401, 403):
                raise ApiError(AUTH_FAILED, "fal rejected the key while fetching this job's result.")
            if result.status >= 400:
                raise from_http_status(result.status, _provider_message(result) or None)
            body = result.json()
            outputs = result_outputs(body)
            if not outputs:
                # A completed job with no URL is a real failure, not an empty
                # success: there is nothing for the user to look at.
                raise ApiError(
                    INVALID_RESPONSE,
                    "fal reported this job complete but returned no output file URL.",
                    details={
                        "provider_job_id": job.provider_job_id,
                        "keys": sorted(body.keys())[:12] if isinstance(body, Mapping) else "",
                    },
                    hint="The job may have been cancelled at the provider. Check the endpoint's history in fal.",
                )
            return JobStatus(
                state=STATE_COMPLETED,
                progress=None,
                provider_status=raw_status or "COMPLETED",
                message=message,
                outputs=outputs,
                raw={"status": raw_status, "result_keys": sorted(body.keys())[:12] if isinstance(body, Mapping) else []},
            )

        if state in (STATE_FAILED, STATE_CANCELLED, STATE_TIMED_OUT):
            error_code = mapping.first_string(payload, mapping.ERROR_KEYS) or (
                (raw_status or state).lower()
            )
            return JobStatus(
                state=state,
                progress=None,
                provider_status=raw_status or state,
                message=message or "fal reported this job as " + state + ".",
                error_code=str(error_code)[:80],
                raw={"status": raw_status},
            )

        return JobStatus(
            state=state,
            progress=None,
            provider_status=raw_status or state,
            message=message,
            queue_position=position,
            raw={"status": raw_status},
        )

    def cancel(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        """Ask fal to cancel. Only a 2xx counts as accepted."""
        cancel_url = _absolute(getattr(job, "cancel_url", None)) or self._rebuild(context, job, "cancel")
        response = _fetch(context, "PUT", cancel_url, headers=_headers_for(context, context.credential))
        if response.status in (401, 403):
            raise ApiError(AUTH_FAILED, "fal rejected the key on this job's cancel route.")
        if response.status == 404:
            raise ApiError(
                ENDPOINT_NOT_FOUND,
                "fal does not know this job, so there is nothing to cancel.",
                details={"provider_job_id": job.provider_job_id},
            )
        if response.status >= 400:
            raise from_http_status(response.status, _provider_message(response) or None)
        payload: Any = None
        try:
            payload = response.json()
        except ApiError:
            payload = None
        raw_status = mapping.first_string(payload, mapping.STATUS_KEYS) if isinstance(payload, Mapping) else ""
        state = translate_status(raw_status) if raw_status else STATE_CANCELLED
        if state in (STATE_QUEUED, STATE_PROCESSING):
            # fal accepted the cancellation request but the job has not stopped
            # yet. Saying "cancelled" here would be a claim we cannot support.
            return JobStatus(
                state=state,
                progress=None,
                provider_status=raw_status,
                message="fal accepted the cancellation request; the job has not stopped yet.",
                raw={"status": raw_status},
            )
        return JobStatus(
            state=STATE_CANCELLED,
            progress=None,
            provider_status=raw_status or "CANCELLED",
            message="fal accepted the cancellation request.",
            raw={"status": raw_status},
        )

    def estimate_cost(
        self, context: AdapterContext, request: GenerationRequest, profile: CapabilityProfile
    ) -> CostEstimate:
        """Quote fal's own pricing prose, and do not turn it into a number.

        fal publishes a per-endpoint pricing sentence in markdown. This platform
        has no contract with fal and no reliable parser for that sentence, so
        the amount stays unknown and the sentence is repeated verbatim. A made-up
        total would be a fabricated cost shown next to a real bill.
        """
        note = pricing_note(context, request.model_key)
        detail = (
            "fal publishes this pricing note for the endpoint: " + note
            if note
            else "fal published no pricing note for this endpoint."
        )
        return CostEstimate(
            currency="",
            amount=None,
            is_estimate=True,
            is_billable=False,
            unit="per request",
            note=detail + " Billed by fal on your own account.",
            unknown_reason=(
                "fal does not publish a machine-readable per-request price through its API, so this "
                "platform cannot total it."
            ),
        )

    def parse_error(self, error: ApiError) -> ApiError:
        """Keep fal's own words, and name the two failures users actually hit."""
        message = (error.provider_message or "").strip()
        lowered = message.lower()
        if error.status == 401 and "invalid key" in lowered:
            return ApiError(
                AUTH_FAILED,
                "fal rejected this API key.",
                status=401,
                provider_message=message,
                hint="Check the key in fal's dashboard, then replace it on this connection.",
            )
        if error.status == 404 and "not_found" in lowered:
            return ApiError(
                ENDPOINT_NOT_FOUND,
                "fal does not know this endpoint or job.",
                status=404,
                provider_message=message,
            )
        return error

    # -- internals ---------------------------------------------------------

    def _catalog_url(self, context: AdapterContext) -> str:
        override = (context.config or {}).get("catalog_url")
        if isinstance(override, str) and override.strip():
            return override.strip()
        return CATALOG_URL

    def _probe_target(self, context: AdapterContext) -> Tuple[str, str]:
        """Where to test the key, and which endpoint the probe names."""
        base = (context.base_url or QUEUE_BASE).strip()
        if is_fal_host(base):
            return self._catalog_url(context), ""
        model = str((context.config or {}).get("test_model") or "").strip("/")
        if model:
            paths = queue_paths(context, model)
            return paths["status"].replace("{request_id}", UNKNOWN_REQUEST_ID), model
        return base.rstrip("/") + "/", ""

    def _probe_params(self) -> Dict[str, Any]:
        return {"limit": 3}

    def _models_seen(self, response: Any) -> Tuple[str, ...]:
        try:
            payload = response.json()
        except ApiError:
            return ()
        items = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            return ()
        out: List[str] = []
        for item in items:
            if isinstance(item, Mapping):
                key = str(item.get("endpoint_id") or "").strip()
                if key:
                    out.append(key)
        return tuple(out[:5])

    def _failed_test(self, error: ApiError, probe: str) -> ConnectionTestResult:
        code = error.code if error.code in CONNECTION_TEST_OUTCOMES else INVALID_RESPONSE
        return ConnectionTestResult(
            outcome=code,
            ok=False,
            message=error.message,
            detail={"probe": probe, "hint": error.hint or ""},
        )

    def _rebuild(self, context: AdapterContext, job: SubmittedJob, kind: str) -> str:
        model_key = str(getattr(job, "model_key", "") or "").strip()
        if not model_key:
            raise ApiError(
                INTERNAL,
                "This job does not record which model it belongs to, so its " + kind + " route cannot be built.",
                details={"provider_job_id": job.provider_job_id},
            )
        path = queue_paths(context, model_key)[kind]
        return path.replace("{request_id}", job.provider_job_id)


# ---------------------------------------------------------------------------
# Routes, interface loading and pricing
# ---------------------------------------------------------------------------


def _absolute(url: Any) -> Optional[str]:
    text = str(url or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return None


def queue_paths(context: AdapterContext, model_key: str) -> Dict[str, str]:
    """The four queue routes for one endpoint.

    fal's convention is used by default and every path can be overridden on the
    connection, which is what makes a fal-compatible self-hosted server usable
    without this adapter having to learn that server's routes.
    """
    base = (context.base_url or QUEUE_BASE).strip().rstrip("/")
    model = (model_key or "").strip().strip("/")
    if not model:
        raise validation_error("A model identifier is required.", field="model_key")
    config = context.config or {}
    defaults = {
        "submit": base + "/" + model,
        "status": base + "/" + model + "/requests/{request_id}/status",
        "result": base + "/" + model + "/requests/{request_id}",
        "cancel": base + "/" + model + "/requests/{request_id}/cancel",
    }
    for name in ("submit", "status", "result", "cancel"):
        override = config.get(name + "_path_template")
        if isinstance(override, str) and override.strip():
            rendered = _render_path_template(override.strip(), model, kind=name)
            if not rendered.startswith(("http://", "https://")):
                rendered = base + "/" + rendered.lstrip("/")
            defaults[name] = rendered
    return defaults


_PATH_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PATH_PLACEHOLDERS = ("model_key", "request_id")


def _render_path_template(template: str, model_key: str, *, kind: str) -> str:
    """Substitute the model identifier, keeping {request_id} for the caller.

    Only the two documented placeholders are accepted. Anything else is a
    configuration error rather than something to be passed through, because a
    template that silently kept a literal brace would produce a request to a
    path the provider does not serve.
    """
    for found in _PATH_PLACEHOLDER.findall(template):
        if found not in _PATH_PLACEHOLDERS:
            raise validation_error(
                "The connection's " + kind + " path template uses an unknown placeholder {"
                + found + "}. Available placeholders are {model_key} and {request_id}.",
                field=kind + "_path_template",
                details={"placeholder": found, "allowed": list(_PATH_PLACEHOLDERS)},
            )
    return template.replace("{model_key}", model_key)


def load_interface(context: AdapterContext, model_key: str) -> FalSchema:
    """Read one endpoint's interface, preferring the provider's live schema.

    The schema route is anonymous, so no credential is sent. When it cannot be
    reached, this repo's curated copy of fal's documents is used instead and the
    resulting profile says so in its notes.
    """
    key = (model_key or "").strip()
    if not key:
        raise validation_error("A model identifier is required.", field="model_key")
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached

    profile: Optional[FalSchema] = None
    failure: Optional[ApiError] = None
    try:
        response = _fetch(context, "GET", schema_url(key), headers=_headers_for(context, None))
        if response.status == 404:
            raise ApiError(
                INVALID_MODEL_ID,
                "fal publishes no endpoint called '" + key + "'.",
                details={"endpoint_id": key},
                hint="Copy the endpoint identifier from the model's page on fal, for example fal-ai/flux-2.",
            )
        if response.status >= 400:
            raise from_http_status(response.status, _provider_message(response) or None)
        profile = compile_schema(response.json(), endpoint_id=key)
    except ApiError as error:
        failure = error

    if profile is None:
        facts = curated_endpoint(key)
        if facts is not None:
            profile = compile_curated(facts)
        elif failure is not None:
            raise failure
        else:  # pragma: no cover - defensive: the try block always sets one
            raise ApiError(INVALID_RESPONSE, "The endpoint's interface could not be read.")

    _remember(_SCHEMA_CACHE, key, profile, SCHEMA_CACHE_LIMIT)
    return profile


def _pricing_item(items: Sequence[Any], model_key: str) -> Optional[Mapping[str, Any]]:
    """The listing record whose id is exactly this endpoint, or None.

    Only an exact id match counts. fal's listing returns neighbouring models
    beside the requested one, and attributing a neighbour's price to this endpoint
    would put another model's bill next to this model's output.
    """
    wanted = (model_key or "").strip()
    if not wanted:
        return None
    for item in items:
        if isinstance(item, Mapping) and str(item.get("id") or "").strip() == wanted:
            return item
    return None


def pricing_note(context: AdapterContext, model_key: str) -> str:
    """fal's own pricing sentence for one endpoint, quoted, or an empty string.

    Fetched anonymously because this is public page copy rather than account data.
    The result is cached only when a page was actually read, so an outage is
    retried instead of being remembered as "no price". An empty answer means the
    price is unknown; the caller reports it that way rather than inventing one.
    """
    key = (model_key or "").strip()
    if not key:
        return ""
    cached = _PRICING_CACHE.get(key)
    if cached is not None:
        return cached
    note = ""
    reached = False
    try:
        for page in range(1, PRICING_PAGE_LIMIT + 1):
            response = _fetch(
                context,
                "GET",
                PRICING_URL,
                headers={},
                params={"endpoint_id": key, "page": page},
            )
            if response.status >= 400:
                break
            payload = response.json()
            items = payload.get("items") if isinstance(payload, Mapping) else None
            if not isinstance(items, list) or not items:
                break
            reached = True
            found = _pricing_item(items, key)
            if found is not None:
                note = _text(found.get("pricingInfoOverride")) or ""
                break
    except ApiError:
        note = ""
    note = " ".join(note.split())
    if reached:
        _remember(_PRICING_CACHE, key, note, PRICING_CACHE_LIMIT)
    return note


def modality_for_category(category: str) -> str:
    """Guess a modality from fal's category label, and only that.

    Used for the catalog list, where a movie-vs-still decision has to be made
    before any schema exists. It never becomes a capability: modes, sizes and
    reference roles still come from the endpoint's own document.
    """
    text = (category or "").strip().lower()
    if not text:
        return "other"
    if "video" in text:
        return "video"
    if "image" in text:
        return "image"
    return "other"
