"""Credential redaction, in one place, applied on the way out.

This module exists because "we remembered not to log the key" is not a security
control -- it is a hope. Everything the platform emits passes through
:func:`scrub`, which is idempotent and cheap, so callers that forget are still
covered by the layers they cannot forget: the logger, the error renderer and the
provider-error normaliser all call it.

The patterns are deliberately broad. Over-redacting a log line costs a debugging
minute; under-redacting one costs a customer's API key.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Mapping, Sequence

#: Registered at runtime: the literal secret values this process holds.
#: A dictionary of live credentials is the only pattern that is always correct.
_registered: List[str] = []
_REGISTERED_MAX = 256

#: Substrings that mark a JSON key as sensitive during structural scrubbing.
SENSITIVE_KEY_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "auth",
    "credential",
    "cookie",
    "signature",
    "session",
    "bearer",
)

#: Field names that merely *look* sensitive but carry no secret. Kept narrow on
#: purpose: a name is only excused after being seen in real provider payloads.
BENIGN_KEYS = frozenset(
    {
        "keyframe",
        "keys",
        "keyframes",
        "public_key_id",
        "apikey_configured",
        "has_key",
        "key_last4",
        "model_key",
        "sort_key",
        "task_key",
        "seed_key",
        "key_of",
    }
)

#: Bearer / Basic / Token authorization values, including the quoted forms that
#: appear inside a JSON error body a provider sent back.
_AUTH_HEADER = re.compile(
    r"""(?ix)
    (?P<prefix>\b(?:authorization|api[-_]?key|x-api-key|x-goog-api-key|bearer|token)\b
        \s*[:=]\s*
        (?:["']\s*)?
        (?:(?:bearer|basic|token)\s+)?
    )
    (?P<value>[A-Za-z0-9_\-\.=+/]{8,})
    """
)

#: Well-known provider key shapes, so an unlabelled leak still gets caught.
_KEY_SHAPES = [
    # fal: "<uuid>:<hex>"
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{16,}\b"),
    # OpenAI / Anthropic / OpenRouter style prefixes
    re.compile(r"\bsk-(?:proj-|ant-|or-)?[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bre_[A-Za-z0-9]{16,}\b"),
    # Signed URL query parameters
    re.compile(r"""(?i)\b(X-Amz-Signature|X-Amz-Credential|Signature|sig|access_token)=[^&\s"']+"""),
    # PEM bodies
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

_REDACTED = "[redacted]"


def register_secret(value: str) -> None:
    """Remember a live secret so it is scrubbed even where no pattern matches.

    Called when a credential is decrypted for use. Bounded and deduplicated so a
    long-lived worker cannot grow it without limit.
    """
    if not value or len(value) < 8:
        return
    if value in _registered:
        return
    if len(_registered) >= _REGISTERED_MAX:
        _registered.pop(0)
    _registered.append(value)


def forget_all_secrets() -> None:
    """Drop the registry. Used by tests to prove the patterns alone suffice."""
    _registered.clear()


def scrub(text: str) -> str:
    """Return `text` with anything resembling a credential replaced."""
    if not text:
        return text
    out = text
    for secret in _registered:
        if secret in out:
            out = out.replace(secret, _REDACTED)
    for pattern in _KEY_SHAPES:
        out = pattern.sub(_REDACTED, out)
    out = _AUTH_HEADER.sub(lambda m: m.group("prefix") + _REDACTED, out)
    return out


def scrub_bytes(data: bytes, limit: int = 4096) -> str:
    """Decode a prefix of `data` for logging without ever raising."""
    try:
        return scrub(data[:limit].decode("utf-8", "replace"))
    except Exception:  # pragma: no cover - decode with replacement cannot raise
        return "<unreadable>"


def is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in BENIGN_KEYS:
        return False
    if lowered in ("key", "keys"):
        # Bare "key" is ambiguous; treat as sensitive (it is one in every
        # provider form the wizard accepts).
        return True
    return any(hint in lowered for hint in SENSITIVE_KEY_HINTS)


def scrub_structure(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a JSON-like structure.

    Used for provider responses and request echoes. Depth-capped so a hostile or
    merely deep payload cannot turn logging into a denial of service.
    """
    if _depth > 8:
        return "<depth limit>"
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_str = key if isinstance(key, str) else str(key)
            if is_sensitive_key(key_str):
                result[key_str] = _REDACTED if item else item
            else:
                result[key_str] = scrub_structure(item, _depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [scrub_structure(item, _depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub(repr(value))


def scrub_headers(headers: Mapping[str, str]) -> dict:
    """Header names and values with credential-bearing ones removed entirely."""
    out = {}
    for name, value in headers.items():
        if is_sensitive_key(name) or name.lower() in ("authorization", "proxy-authorization"):
            out[name] = _REDACTED
        else:
            out[name] = scrub(str(value))
    return out


def redact_url(url: str) -> str:
    """A URL with any userinfo and any credential-shaped query parameter removed.

    Provider output URLs are frequently pre-signed. We need to log *which* host
    answered, and nothing more.
    """
    if not url:
        return url
    cleaned = scrub(url)
    # strip userinfo even when it did not look like a key
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/@]*@)?(.*)$", cleaned)
    if match and match.group(2):
        cleaned = match.group(1) + "[userinfo]" + "@" + match.group(3)
    return cleaned


def shape_only(value: Any) -> Any:
    """Replace every leaf with its type name. For logging a payload's shape."""
    if isinstance(value, Mapping):
        return {str(k): shape_only(v) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [shape_only(v) for v in value[:10]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string(%d)" % len(value)
    return type(value).__name__
