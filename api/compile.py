"""POST /api/compile — run an open-source prompt expander over your idea.

Body: {prompt, style, model?, dry_run?}

The expander system prompts are the verbatim ones shipped by the model owners
(Wan 2.1 / Wan 2.2, HunyuanVideo, Kandinsky). Dry run is the default and returns
the exact request that would be sent.

Remote execution is opt-in and needs three env vars: ALLOW_REMOTE_COMPILE=1,
COMPILE_GATEWAY_URL, COMPILE_API_KEY. Without them this endpoint never makes an
outbound call — a public deployment must not be able to spend someone's key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promptlayer import build_compile_request, expander_styles  # noqa: E402
from promptlayer.http import (  # noqa: E402
    compile_is_enabled,
    read_json,
    send_error_json,
    send_json,
    validate_prompt,
)


def _call_upstream(request_body: dict) -> dict:
    url = os.environ["COMPILE_GATEWAY_URL"].rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["COMPILE_API_KEY"],
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=110) as resp:
        return json.loads(resp.read().decode("utf-8"))


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = read_json(self)
        if body is None:
            return send_error_json(self, 400, "Body must be JSON under 64 KB")
        prompt = validate_prompt(self, body.get("prompt"))
        if prompt is None:
            return
        style = body.get("style")
        if not isinstance(style, str) or not style:
            return send_error_json(self, 400, "Field 'style' is required")
        try:
            built = build_compile_request(
                prompt=prompt,
                style=style,
                model=body.get("model") or os.getenv("COMPILE_MODEL", "gpt-4o-mini"),
            )
        except KeyError:
            return send_error_json(self, 400, f"Unknown style '{style}'", available=expander_styles())

        dry_run = body.get("dry_run", True)
        if dry_run or not compile_is_enabled():
            return send_json(self, 200, {
                "success": True,
                "data": {
                    **built,
                    "dry_run": True,
                    "compiled": None,
                    "reason": "dry_run requested" if dry_run else
                              "remote compile is not enabled on this deployment",
                },
            })
        try:
            upstream = _call_upstream(built["request"])
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            return send_error_json(self, 502, f"Upstream error {exc.code}", detail=detail)
        except Exception as exc:  # network, timeout, bad JSON
            return send_error_json(self, 502, f"Upstream call failed: {exc}")
        try:
            compiled = upstream["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return send_error_json(self, 502, "Upstream response had no completion")
        send_json(self, 200, {
            "success": True,
            "data": {**built, "dry_run": False, "compiled": compiled, "usage": upstream.get("usage")},
        })

    def do_OPTIONS(self) -> None:  # noqa: N802
        send_json(self, 204, {})

    def log_message(self, *args):
        return
