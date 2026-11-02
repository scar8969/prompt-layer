"""MCP access to the preset catalogue and the Director's compiler.

What this is
------------
Model Context Protocol is a JSON-RPC conversation over a pipe. This module is the
*dispatcher*: it answers the protocol's own methods and exposes the catalogue as
tools, with no network, no database and no provider. `scripts/preset_mcp.py`
wraps it in stdio, which is the transport an MCP client speaks.

What it deliberately is not
---------------------------
It is not a second API. Every tool reads the same documents the HTTP routes read
(`studio/presets_data.py`, `studio/cinema.py`, `promptlayer/`), so a preset
described here and a preset rendered by `/api/presets` cannot disagree. It also
carries no credential and cannot queue a generation: an MCP client can look and
compile, and must use the HTTP API (with a session) to spend.

The catalogue tools
-------------------
``list_presets``      the catalogue, filtered by modality/category/text
``get_preset``        one preset, by key
``compile_brief``     the Director's Panel compiler, deterministically
``list_camera_moves`` the moves and what each adds
``prompt_layers``     what a provider adds to a prompt, for one endpoint

The protocol methods
--------------------
``initialize``, ``tools/list`` and ``tools/call`` are implemented. An unknown
method is answered with JSON-RPC's own "method not found", not a fabricated
success, and an unknown tool is a tool error rather than an empty result.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from . import cinema
from .adapters.base import CAMERA_MOVES
from .errors import ApiError
from .presets_data import PRESETS, PRESETS_BY_KEY, all_category_labels, presets_for

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "prompt-layer"
SERVER_VERSION = "1.0.0"

#: The tool schema is plain JSON Schema, which is what MCP clients read.
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_presets",
        "description": (
            "List the sourced prompt presets in the catalogue. Every preset is a "
            "document with a prompt template, a negative template and settings; "
            "none of them is a generated picture."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "modality": {"type": "string", "enum": ["image", "video"]},
                "category": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    },
    {
        "name": "get_preset",
        "description": "Read one catalogue preset by its key.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "compile_brief",
        "description": (
            "Compile a Director's Panel brief into one prompt. Deterministic: the "
            "same brief always produces the same prompt, and every clause names "
            "the control that produced it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "setting": {"type": "string"},
                "modality": {"type": "string", "enum": ["image", "video"]},
            },
            "additionalProperties": True,
        },
    },
    {
        "name": "list_camera_moves",
        "description": (
            "The camera moves this platform can express, and whether the move is "
            "a native field on some models or prompt wording."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "prompt_layers",
        "description": (
            "What a documented endpoint adds to a prompt before it runs: rewrites, "
            "expansions, canned negatives and the parameter that turns each off."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"model_id": {"type": "string"}},
            "required": ["model_id"],
        },
    },
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_content(value: Any) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]


def _call_list_presets(arguments: Mapping[str, Any]) -> Any:
    modality = _text(arguments.get("modality")) or "image"
    if modality not in ("image", "video"):
        raise ValueError("'modality' must be 'image' or 'video'.")
    category = _text(arguments.get("category"))
    needle = _text(arguments.get("query")).lower()
    limit = int(arguments.get("limit") or 40)
    items = []
    for entry in presets_for(modality):
        if category and entry.get("category") != category:
            continue
        if needle:
            haystack = " ".join(
                str(entry.get(field) or "")
                for field in ("name", "description", "prompt_template", "category")
            ).lower()
            if needle not in haystack:
                continue
        items.append(
            {
                "key": entry["key"],
                "name": entry.get("name"),
                "modality": entry.get("modality"),
                "category": entry.get("category"),
                "aspect_ratio": entry.get("aspect_ratio"),
                "prompt_template": entry.get("prompt_template"),
                "negative_template": entry.get("negative_template") or "",
                "parameters": entry.get("parameters") or {},
                "tags": entry.get("tags") or [],
            }
        )
        if len(items) >= max(1, min(limit, 200)):
            break
    return {
        "presets": items,
        "count": len(items),
        "categories": all_category_labels(modality),
        "source": "studio/presets_data.py (vendored; every entry carries its source)",
    }


def _call_get_preset(arguments: Mapping[str, Any]) -> Any:
    key = _text(arguments.get("key"))
    if not key:
        raise ValueError("'key' is required.")
    entry = PRESETS_BY_KEY.get(key)
    if entry is None:
        raise ValueError("No preset with the key %r." % key)
    return {"preset": entry}


def _call_compile_brief(arguments: Mapping[str, Any]) -> Any:
    brief = {key: value for key, value in arguments.items() if value is not None}
    modality = _text(brief.get("modality")) or "image"
    if modality not in ("image", "video"):
        raise ValueError("'modality' must be 'image' or 'video'.")
    brief["modality"] = modality
    # The HTTP route validates the closed vocabularies before compiling; the tool
    # calls the same helper, so a stale option is refused by name rather than
    # silently dropped into a prompt that looks complete.
    cinema.validate_brief(brief)
    return {"compiled": cinema.compile_brief(brief)}


def _call_list_camera_moves(_arguments: Mapping[str, Any]) -> Any:
    return {
        "camera_moves": [
            {
                "key": move.key,
                "label": move.label,
                "phrase": move.phrase,
                "is_native": move.is_native,
                "parameter": move.parameter,
                "value": move.value,
                "note": "" if move.is_native else "Sent as prompt wording; this model has no camera field.",
            }
            for move in CAMERA_MOVES
        ]
    }


def _call_prompt_layers(arguments: Mapping[str, Any]) -> Any:
    """The provider-layer dataset, for one documented endpoint.

    Imported lazily because `promptlayer` is a sibling package the studio only
    needs when a caller asks this question. An unknown model is refused rather
    than answered with an empty layer list.
    """
    model_id = _text(arguments.get("model_id"))
    if not model_id:
        raise ValueError("'model_id' is required.")
    try:
        from promptlayer import build_preflight, model_slugs
    except Exception:  # noqa: BLE001 - the dataset is optional at runtime
        raise ValueError("The prompt-layer dataset is not installed in this environment.") from None
    candidates = [model_id]
    if model_id.startswith("builtin:"):
        candidates.append(model_id.split(":", 1)[1])
    candidates.append(model_id.split("/")[-1])
    for probe in candidates:
        if probe in model_slugs():
            return {"model": probe, "preflight": build_preflight(probe, "", {})}
    raise ValueError("No documented prompt layers for %r." % model_id)


_CALLS = {
    "list_presets": _call_list_presets,
    "get_preset": _call_get_preset,
    "compile_brief": _call_compile_brief,
    "list_camera_moves": _call_list_camera_moves,
    "prompt_layers": _call_prompt_layers,
}


def dispatch(method: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Answer one JSON-RPC method. Returns a JSON-RPC response object.

    The dispatcher is pure: the same request always produces the same response,
    and nothing is written anywhere. That is what makes it safe to expose over a
    pipe with no authentication -- it cannot spend, change or leak.
    """
    params = dict(params or {})
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = _text(params.get("name"))
        arguments = params.get("arguments") or {}
        if name not in _CALLS:
            return {
                "content": [{"type": "text", "text": "No tool called %r." % name}],
                "isError": True,
            }
        try:
            result = _CALLS[name](arguments if isinstance(arguments, Mapping) else {})
        except (ValueError, ApiError) as error:
            return {"content": [{"type": "text", "text": str(getattr(error, "message", error))}], "isError": True}
        return {"content": _json_content(result)}
    if method == "notifications/initialized":
        return {}
    if method == "ping":
        return {}
    raise _MethodNotFound(method)


class _MethodNotFound(Exception):
    """Raised for a method this server does not implement."""

    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


def handle(message: Mapping[str, Any]) -> Dict[str, Any]:
    """Handle one JSON-RPC message, including its error envelope.

    A request without an id is a notification and gets no response, which is what
    the protocol asks for; the caller writes nothing in that case.
    """
    request_id = message.get("id")
    method = str(message.get("method") or "")
    try:
        result = dispatch(method, message.get("params") or {})
    except _MethodNotFound as error:
        return _error(request_id, -32601, "Method not found: %s" % error.method)
    except Exception as error:  # noqa: BLE001 - a broken tool must not kill the pipe
        return _error(request_id, -32603, "Internal error: %s" % type(error).__name__)
    if "content" in result or "tools" in result or "protocolVersion" in result:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
