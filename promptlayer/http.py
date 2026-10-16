"""Shared plumbing for the Vercel Python functions.

Vercel's Python runtime expects a `handler` that subclasses
BaseHTTPRequestHandler (exported from each file in /api). This module holds the
JSON read/write helpers so each endpoint stays a few lines.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

MAX_BODY = 64 * 1024
MAX_PROMPT = 4000
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


def send_json(handler: Any, code: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for key, value in CORS.items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def send_error_json(handler: Any, code: int, message: str, **extra: Any) -> None:
    send_json(handler, code, {"success": False, "message": message, **extra})


def read_json(handler: Any) -> Optional[Dict[str, Any]]:
    """Parse the request body. None means malformed or oversized."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None
    if length <= 0 or length > MAX_BODY:
        return None
    try:
        payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def compile_is_enabled() -> bool:
    """Remote compile needs an explicit opt-in *and* credentials.

    On a public deployment an API key in the environment means anyone can spend
    it. Requiring ALLOW_REMOTE_COMPILE=1 makes that a deliberate decision rather
    than a side effect of adding a key.
    """
    return (
        os.getenv("ALLOW_REMOTE_COMPILE") == "1"
        and bool(os.getenv("COMPILE_GATEWAY_URL"))
        and bool(os.getenv("COMPILE_API_KEY"))
    )


def validate_prompt(handler: Any, value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        send_error_json(handler, 400, "Field 'prompt' is required")
        return None
    if len(value) > MAX_PROMPT:
        send_error_json(handler, 400, f"Prompt must be <= {MAX_PROMPT} characters")
        return None
    return value


MAX_SECONDS = 600


def seconds_field(handler: Any, value: Any, field: str) -> Optional[float]:
    """A clip length the caller sent, or None. A non-number is refused, not cast.

    `float("abc")` raises, and an unhandled raise inside a handler closes the
    connection — the client sees a 502 with no explanation rather than a 400 that
    says which field was wrong.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        send_error_json(handler, 400, f"Field '{field}' must be a number of seconds")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        send_error_json(handler, 400, f"Field '{field}' must be a number of seconds")
        return None
    if not 0 < number <= MAX_SECONDS:
        send_error_json(handler, 400,
                        f"Field '{field}' must be between 0 and {MAX_SECONDS} seconds")
        return None
    return number


def object_field(handler: Any, value: Any, field: str) -> bool:
    """True when the field is absent or an object; sends a 400 and returns False otherwise."""
    if value is None or isinstance(value, dict):
        return True
    send_error_json(handler, 400, f"Field '{field}' must be an object")
    return False


def list_field(handler: Any, value: Any, field: str) -> bool:
    """True when the field is absent or a list; sends a 400 and returns False otherwise."""
    if value is None or isinstance(value, list):
        return True
    send_error_json(handler, 400, f"Field '{field}' must be a list")
    return False


def string_field(handler: Any, value: Any, field: str) -> bool:
    """True when the field is absent or a string; sends a 400 and returns False otherwise."""
    if value is None or isinstance(value, str):
        return True
    send_error_json(handler, 400, f"Field '{field}' must be a string")
    return False


def design_field(handler: Any, value: Any) -> bool:
    """Validate the documented shape of a 'design' object; False means a 400 was sent.

    The library tolerates a malformed shape so it can never crash, but the API is
    the boundary where the caller can be told: a `selections` that is not an object
    or a `negative` that is not a list is a bug in the client, and saying so is more
    use than quietly dropping it.
    """
    if not object_field(handler, value, "design"):
        return False
    if not isinstance(value, dict):
        return True
    if not object_field(handler, value.get("selections"), "design.selections"):
        return False
    if not list_field(handler, value.get("negative"), "design.negative"):
        return False
    for key in ("palette", "template"):
        if not string_field(handler, value.get(key), f"design.{key}"):
            return False
    return True


def seconds_fields(handler: Any, body: Dict[str, Any], *fields: str) -> Optional[Dict[str, float]]:
    """Validate a group of seconds fields at once.

    None means a field was rejected and the 400 has already been sent, so the
    caller must stop rather than build a response and send a second one. A field
    the caller did not send is simply absent from the result.
    """
    out: Dict[str, float] = {}
    for field in fields:
        value = body.get(field)
        number = seconds_field(handler, value, field)
        if number is None:
            if value not in (None, ""):
                return None
            continue
        out[field] = number
    return out
