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
