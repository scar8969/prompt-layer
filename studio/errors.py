"""Diagnostic codes, and the exception that carries them to the edge.

A single module for every failure the platform can report, because the failure
vocabulary is shared by four audiences that must agree: the adapter that
detected the problem, the worker that recorded it on a job, the HTTP layer that
turns it into a response, and the UI that has to tell a person what to do next.

The shape of an error
---------------------
Every failure has a **code** (stable, machine-readable, safe to branch on), a
**message** (one sentence, written for the person who hit it), and usually a
**hint** (the concrete next step). Optionally it carries **details** for the UI
and a **provider_message** -- the provider's own words, scrubbed of secrets
before it is stored or returned.

What is deliberately absent: stack traces, request bodies, and credentials.
Nothing in this module will ever echo a submitted key back to the client.

Connection test outcomes
------------------------
The connection wizard offers the user a finite list of outcomes, so the codes for
provider-side failures are declared here as named constants and used verbatim by
the adapters. That is what makes "authentication failed" a different screen from
"the endpoint does not exist" instead of one generic red box.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

# -- Provider / connection diagnostics -------------------------------------
#: The provider rejected the credential. The key is wrong, expired or revoked.
AUTH_FAILED = "auth_failed"
#: The base URL answered, but not with the API surface we asked for.
ENDPOINT_NOT_FOUND = "endpoint_not_found"
#: The provider exists and the key works, but this model does not offer the
#: operation the caller requested.
UNSUPPORTED_OPERATION = "unsupported_operation"
#: The model identifier is not one the provider knows about.
INVALID_MODEL_ID = "invalid_model_id"
#: The provider is throttling us. Retryable after a delay.
RATE_LIMITED = "rate_limited"
#: No answer inside the configured budget. Retryable.
TIMEOUT = "timeout"
#: A response arrived but could not be understood: wrong content type, missing
#: field, an HTML error page where JSON was expected.
INVALID_RESPONSE = "invalid_response"
#: Egress policy refused the destination: http where https is required, a
#: blocked address range, too many redirects, or an over-large body.
NETWORK_RESTRICTED = "network_restricted"
#: The provider reported a failure of its own. The message is passed through.
PROVIDER_ERROR = "provider_error"
#: The provider says the account cannot pay for this request.
INSUFFICIENT_BALANCE = "insufficient_balance"
#: A returned artifact could not be fetched or stored.
OUTPUT_DOWNLOAD_FAILED = "output_download_failed"
STORAGE_FAILED = "storage_failed"

# -- Request / domain diagnostics ------------------------------------------
MALFORMED_URL = "malformed_url"
INSECURE_URL = "insecure_url"
VALIDATION_FAILED = "validation_failed"
UNAUTHENTICATED = "unauthenticated"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
#: The model cannot do what was asked. Distinct from UNSUPPORTED_OPERATION,
#: which is the *provider* saying no; this is us refusing before we spend money.
CAPABILITY_UNSUPPORTED = "capability_unsupported"
#: The deployment is missing configuration a feature needs.
CONFIGURATION_REQUIRED = "configuration_required"
INTERNAL = "internal_error"

#: HTTP status per code. A provider failure is not automatically a 500: the
#: request was well-formed, so the status reflects who needs to act.
_STATUS: Dict[str, int] = {
    AUTH_FAILED: 401,
    ENDPOINT_NOT_FOUND: 404,
    UNSUPPORTED_OPERATION: 400,
    INVALID_MODEL_ID: 400,
    RATE_LIMITED: 429,
    TIMEOUT: 504,
    INVALID_RESPONSE: 502,
    NETWORK_RESTRICTED: 400,
    PROVIDER_ERROR: 502,
    INSUFFICIENT_BALANCE: 402,
    OUTPUT_DOWNLOAD_FAILED: 502,
    STORAGE_FAILED: 500,
    MALFORMED_URL: 400,
    INSECURE_URL: 400,
    VALIDATION_FAILED: 400,
    UNAUTHENTICATED: 401,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    CONFLICT: 409,
    RATE_LIMIT_EXCEEDED: 429,
    CAPABILITY_UNSUPPORTED: 400,
    CONFIGURATION_REQUIRED: 503,
    INTERNAL: 500,
}

#: The next step, phrased as an instruction rather than an apology.
_HINTS: Dict[str, str] = {
    AUTH_FAILED: "Check the API key and its permissions, then test the connection again.",
    ENDPOINT_NOT_FOUND: "Check the base URL. It should point at the API root, without a path to a specific endpoint.",
    UNSUPPORTED_OPERATION: "Pick a model that supports this operation, or switch to a mode this model offers.",
    INVALID_MODEL_ID: "Copy the model identifier exactly as the provider publishes it.",
    RATE_LIMITED: "Wait a moment and try again; the provider is limiting how fast requests arrive.",
    TIMEOUT: "The provider did not answer in time. Try again, or raise the read timeout if the model is simply slow.",
    INVALID_RESPONSE: "The provider replied with something unexpected. Check that the base URL is the provider's API and not its website.",
    NETWORK_RESTRICTED: "This address is blocked by egress policy. If this is a self-hosted server, add its host to the connection's allowed hosts.",
    PROVIDER_ERROR: "The provider reported an error. The message above is theirs, unedited.",
    INSUFFICIENT_BALANCE: "The provider account has no remaining balance.",
    OUTPUT_DOWNLOAD_FAILED: "The result was produced, but could not be retrieved. Use the provider URL on the job to fetch it manually.",
    STORAGE_FAILED: "The file could not be written to storage. Check storage configuration and available space.",
    MALFORMED_URL: "Enter a full URL, for example https://api.example.com/v1.",
    INSECURE_URL: "Use https for any endpoint reachable over the public internet.",
    VALIDATION_FAILED: "Correct the highlighted fields and submit again.",
    UNAUTHENTICATED: "Sign in again.",
    FORBIDDEN: "This action is not permitted for your role in this workspace.",
    NOT_FOUND: "The item may have been deleted.",
    CONFLICT: "Something with that name already exists in this workspace.",
    RATE_LIMIT_EXCEEDED: "Slow down; too many requests arrived in a short window.",
    CAPABILITY_UNSUPPORTED: "Enable a model that supports this, or choose a different mode.",
    CONFIGURATION_REQUIRED: "Set the environment variables named below and restart the service.",
    INTERNAL: "This is a bug. The request id in the response identifies the occurrence in the logs.",
}

#: Failures worth trying again unchanged. A rejected key or a bad model id is
#: not: retrying those just spends the user's rate limit.
RETRYABLE_CODES = frozenset({RATE_LIMITED, TIMEOUT, PROVIDER_ERROR, INTERNAL})

#: Failures the connection wizard reports as a distinct outcome.
CONNECTION_TEST_OUTCOMES = (
    AUTH_FAILED,
    ENDPOINT_NOT_FOUND,
    UNSUPPORTED_OPERATION,
    INVALID_MODEL_ID,
    RATE_LIMITED,
    TIMEOUT,
    INVALID_RESPONSE,
    NETWORK_RESTRICTED,
    PROVIDER_ERROR,
    INTERNAL,
)


class ApiError(Exception):
    """A failure with a code the client can branch on and a sentence for a human.

    Deliberately not carrying the request body or the submitted credential. When
    a provider's own text is relevant it goes in `provider_message`, which has
    already been through the redactor.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: Optional[int] = None,
        hint: Optional[str] = None,
        details: Optional[Any] = None,
        provider_message: Optional[str] = None,
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code or INTERNAL
        self.message = message or "Something went wrong."
        self.status = status if status is not None else _STATUS.get(self.code, 500)
        self.hint = hint if hint is not None else _HINTS.get(self.code)
        self.details = details
        self.provider_message = provider_message
        #: Which form field the failure belongs to, so the UI can put the message
        #: next to the input instead of in a banner at the top.
        self.field = field
        self.request_id: Optional[str] = None

    # -- classification -----------------------------------------------------
    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def with_request_id(self, request_id: str) -> "ApiError":
        self.request_id = request_id
        return self

    # -- rendering ----------------------------------------------------------
    def to_payload(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """The wire form. Keys are stable; the UI reads them by name."""
        body: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.hint:
            body["hint"] = self.hint
        if self.details is not None:
            body["details"] = self.details
        if self.provider_message:
            body["provider_message"] = self.provider_message
        if self.field:
            body["field"] = self.field
        body["retryable"] = self.retryable
        identifier = request_id or self.request_id
        if identifier:
            body["request_id"] = identifier
        return {"error": body}

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return "%s: %s" % (self.code, self.message)


class ConfigError(RuntimeError):
    """Configuration is missing or contradictory, detected before serving.

    Raised at startup rather than converted into an ApiError: a deployment that
    cannot work should say so once, loudly, instead of answering every request
    with a 503 that looks like a transient fault.
    """

    def __init__(self, message: str, *, variables: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.variables = list(variables or [])


# -- Constructors for the common cases -------------------------------------
# These exist so call sites read as intent rather than as string assembly, and
# so the wording stays consistent when the same condition is detected in two
# different modules.


def validation_error(message: str, *, field: Optional[str] = None, details: Any = None) -> ApiError:
    return ApiError(VALIDATION_FAILED, message, field=field, details=details)


def not_found(what: str) -> ApiError:
    return ApiError(NOT_FOUND, "That %s could not be found." % what)


def forbidden(message: str = "You do not have access to this item.") -> ApiError:
    return ApiError(FORBIDDEN, message)


def unauthenticated(message: str = "Sign in to continue.") -> ApiError:
    return ApiError(UNAUTHENTICATED, message)


def conflict(message: str) -> ApiError:
    return ApiError(CONFLICT, message)


def configuration_required(feature: str, variables: List[str]) -> ApiError:
    """Something a deployment must configure before a feature can work.

    The variable names are included in the response on purpose. Telling an
    operator which variable is missing is worth more than the small amount of
    configuration shape it reveals, and none of them are secret *values*.
    """
    return ApiError(
        CONFIGURATION_REQUIRED,
        "%s is not configured in this deployment." % feature.replace("_", " ").capitalize(),
        details={"feature": feature, "variables": variables},
    )


def capability_unsupported(message: str, *, details: Any = None) -> ApiError:
    return ApiError(CAPABILITY_UNSUPPORTED, message, details=details)


def provider_error(
    message: str,
    *,
    code: str = PROVIDER_ERROR,
    status: Optional[int] = None,
    provider_message: Optional[str] = None,
) -> ApiError:
    return ApiError(code, message, status=status, provider_message=provider_message)


def from_http_status(status: int, provider_message: Optional[str] = None) -> ApiError:
    """Map a provider's HTTP status onto our diagnostic vocabulary.

    Providers vary in how they signal failure, but the status code is the one
    signal almost all of them share, so it is the baseline the adapters fall back
    to when the body offers nothing more specific.
    """
    if status == 401:
        return ApiError(
            AUTH_FAILED,
            "The provider rejected the credential.",
            provider_message=provider_message,
        )
    if status == 403:
        return ApiError(
            AUTH_FAILED,
            "The provider refused the request with this credential.",
            hint="The key may lack permission for this model, or is restricted by IP.",
            provider_message=provider_message,
        )
    if status == 404:
        return ApiError(
            ENDPOINT_NOT_FOUND,
            "The provider does not have that endpoint or model.",
            provider_message=provider_message,
        )
    if status == 402:
        return ApiError(
            INSUFFICIENT_BALANCE, "The provider account cannot pay for this request."
        )
    if status == 429:
        return ApiError(
            RATE_LIMITED,
            "The provider is rate limiting this key.",
            provider_message=provider_message,
        )
    if 500 <= status < 600:
        return ApiError(
            PROVIDER_ERROR,
            "The provider reported a server error.",
            provider_message=provider_message,
        )
    if 400 <= status < 500:
        return ApiError(
            VALIDATION_FAILED,
            "The provider rejected the request as invalid.",
            provider_message=provider_message,
        )
    return ApiError(INVALID_RESPONSE, "The provider returned an unusable response.")


def describe_outcomes() -> List[Mapping[str, Any]]:
    """The closed set of connection-test outcomes, for the wizard and the docs.

    Exposed as data so the frontend renders the same list the backend can emit;
    a new code can never appear in a response without also appearing in the UI's
    legend.
    """
    return [
        {
            "code": code,
            "status": _STATUS.get(code, 500),
            "hint": _HINTS.get(code),
            "retryable": code in RETRYABLE_CODES,
        }
        for code in CONNECTION_TEST_OUTCOMES
    ]
