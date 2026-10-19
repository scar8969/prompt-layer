"""User-defined HTTP providers, described as data and interpreted, never executed.

Every other adapter in this package is a class written for one provider protocol.
This one is not. It exists so a user can connect any HTTP API that generates media
without this project shipping a Python file for it, by filling in a mapping: which
URL to submit to, where the request id lives, which word the provider uses for
"done", which field carries the output URL.

Three rules keep a user-supplied mapping from becoming a way to run code.

One. The mapping is data. Paths are strings, fields are strings, enums are closed
sets. A key that is not in the shape below is refused when the connection is saved,
with the offending key named, rather than being passed through to be discovered at
request time.

Two. Templates are not evaluated. A path may contain only {request_id} and {model},
and substitution is a string replace. There is no expression language, no call, no
attribute access, no format specification, and therefore no path from a mapping to
the interpreter.

Three. A mapping cannot invent anything. If the provider answered and the answer
does not contain an output the mapping describes, the job fails with the provider
payload attached. It never falls back to a plausible-looking URL, and it never
reports a completion the provider did not state.

The consequence to accept honestly: a wrong mapping produces a clear failure, not a
successful-looking wrong image. That is the correct trade for a feature whose whole
purpose is to let strangers point this server at endpoints of their choosing.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..errors import (
    CONNECTION_TEST_OUTCOMES,
    ENDPOINT_NOT_FOUND,
    INTERNAL,
    INVALID_RESPONSE,
    PROVIDER_ERROR,
    STORAGE_FAILED,
    TIMEOUT,
    UNSUPPORTED_OPERATION,
    VALIDATION_FAILED,
    ApiError,
    AUTH_FAILED,
    CAPABILITY_UNSUPPORTED,
    CONFIGURATION_REQUIRED,
)
from ..util import json_dumps, json_loads
from .base import ParameterSchema
from .core import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_PROCESSING,
    STATE_QUEUED,
    STATE_TIMED_OUT,
    ModelAdapter,
    _error_from_response,
    register,
)
from .mapping import (
    ID_KEYS,
    MESSAGE_KEYS,
    QUEUE_KEYS,
    STATUS_KEYS,
    URL_KEYS,
    as_list,
    collect_urls,
    drop_empty,
    first_present,
    first_string,
    get_path,
    join_url,
    percent_to_fraction,
    to_float,
    to_int,
    unique,
)
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

#: How the provider is shaped. This is the one choice the user cannot avoid,
#: because it decides which of the blocks below are required.
#:
#:   queued      submit returns an id, then the id is polled until it is done
#:   sync        submit returns the finished output in the same response
#:   upload      submit returns the finished output on a follow-up GET
#:   selectable  submit returns a list of candidate ids to choose from
#:   openai_images  the OpenAI images API: submit returns data[0].b64_json
STRUCTURE_TYPES = ("queued", "sync", "upload", "selectable", "openai_images")

RESPONSE_TYPES = ("json", "binary")

#: How the submit body is encoded. `json` is the default and was previously
#: missing from the accepted list while `_blank_spec()` defaulted to it, so a
#: mapping that said nothing about its body type could not be loaded at all.
#: `url` is kept for the form-encoded bodies existing mappings already store.
SUBMIT_BODY_TYPES = ("json", "url", "base64", "multipart")

#: How one reference image is carried in the body. A JSON body still carries
#: references, so `json` is accepted here too; the submit builder treats it the
#: same way it treats `url`.
REFERENCE_ENCODINGS = ("url", "base64", "multipart", "json")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

#: The only placeholders a mapping may use. Two names, both facts this platform
#: already holds, and neither of them user text.
PATH_PLACEHOLDERS = ("request_id", "model")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.\[\]-]{0,120}$")
_JSON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")

#: Refuse to read more than this from a provider in one response. A media
#: response is the only unbounded thing a declarative provider can return.
MAX_BINARY_BYTES = 32 * 1024 * 1024

#: How much of a reference image this server will read in order to inline it into
#: a request. Inlining is what makes a base64 or multipart provider work at all,
#: so a hard cap here is the difference between a working integration and an
#: out-of-memory crash on a hostile URL.
MAX_INLINE_BYTES = 12 * 1024 * 1024

#: Poll cadence when the provider states none.
DEFAULT_POLL_SECONDS = 2.0

#: How many candidates a selectable provider may offer at once.
MAX_CANDIDATES = 8

#: Status words this platform recognises when the provider is not configured to
#: say otherwise. Deliberately generous on "done" and deliberately narrow on
#: "failed": an unknown word is treated as still-working, never as success.
DEFAULT_SUCCEEDED = ("succeeded", "success", "completed", "complete", "done", "finished")
DEFAULT_FAILED = ("failed", "failure", "error", "errored")
DEFAULT_CANCELLED = ("cancelled", "canceled")
DEFAULT_PROCESSING = (
    "processing",
    "running",
    "in_progress",
    "inprogress",
    "pending",
    "queued",
    "starting",
    "submitted",
)


def _blank_spec() -> Dict[str, Any]:
    """The full default mapping, and the allowlist of keys a user may set."""
    return {
        "structure": "queued",
        "submit": {
            "path": "",
            "method": "POST",
            "body_type": "json",
            "model_field": "model",
            "prompt_field": "prompt",
            "negative_prompt_field": "",
            "prompt_instructions_field": "",
            "count_field": "",
            "seed_field": "",
            "mask_field": "",
            "parameters_field": "",
            "extra_fields": {},
            "extra_headers": {},
            "references": [],
        },
        "status": {
            "path": "",
            "method": "GET",
            "request_id_path": "request_id",
            "status_path": "status",
            "progress_path": "",
            "message_path": "",
            "queue_position_path": "",
            "retry_after_path": "",
            "poll_after_seconds": DEFAULT_POLL_SECONDS,
            "succeeded_values": list(DEFAULT_SUCCEEDED),
            "failed_values": list(DEFAULT_FAILED),
            "cancelled_values": list(DEFAULT_CANCELLED),
            "processing_values": list(DEFAULT_PROCESSING),
        },
        "result": {
            "path": "",
            "method": "GET",
            "outputs_path": "",
            "output_url_path": "",
            "output_type_path": "",
            "output_width_path": "",
            "output_height_path": "",
            "output_duration_path": "",
            "output_seed_path": "",
            "id_path": "",
            "result_ready_path": "",
            "inline_in_status": True,
        },
        "response": {
            "type": "json",
            "outputs_path": "",
            "output_url_path": "",
            "output_type_path": "",
            "binary_content_type": "",
        },
        "cancel": {
            "path": "",
            "method": "POST",
        },
        "models": {
            "path": "",
            "ids_path": "",
            "id_path": "id",
            "name_path": "",
        },
        "test": {
            "path": "",
            "method": "GET",
        },
        "errors": {
            "message_paths": ["error.message", "error.detail", "message", "detail", "error"],
        },
    }


DEFAULT_SPEC: Dict[str, Any] = _blank_spec()

#: Blocks whose string values are URLs and therefore need the strict check below.
_PATH_KEYS = (
    ("submit", "path"),
    ("status", "path"),
    ("result", "path"),
    ("cancel", "path"),
    ("models", "path"),
    ("test", "path"),
)

_METHOD_KEYS = (
    ("submit", "method"),
    ("status", "method"),
    ("result", "method"),
    ("cancel", "method"),
    ("test", "method"),
)

#: Blocks that are only meaningful for some structures. A key left at its blank
#: default is simply unused, but a required one that is blank is a save-time error.
_REQUIRED_BY_STRUCTURE: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "queued": (("submit", "path"), ("status", "path")),
    "selectable": (("submit", "path"), ("status", "path")),
    "sync": (("submit", "path"),),
    "upload": (("submit", "path"), ("result", "path")),
    "openai_images": (("submit", "path"),),
}


def _fail(message: str, *, key: str, code: str = VALIDATION_FAILED, hint: str = "") -> ApiError:
    return ApiError(
        code,
        message,
        details={"key": key},
        hint=hint or None,
        field=key,
    )


def _clean_path(value: Any, key: str) -> str:
    """A URL fragment this server is willing to call on a user behalf."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise _fail("The path for %s must be text." % key, key=key)
    candidate = value.strip()
    if len(candidate) > 500:
        raise _fail("The path for %s is unreasonably long." % key, key=key)
    if any(ch.isspace() for ch in candidate) or "\\" in candidate:
        raise _fail(
            "The path for %s contains a space or a backslash." % key,
            key=key,
            hint="Paths are URL fragments: use forward slashes and no whitespace.",
        )
    absolute = candidate.startswith("https://") or candidate.startswith("http://")
    if not absolute and not candidate.startswith("/"):
        raise _fail(
            "The path for %s must start with a forward slash, or be a full https URL." % key,
            key=key,
        )
    if candidate.startswith("//"):
        raise _fail(
            "The path for %s starts with two slashes, which changes the host." % key,
            key=key,
        )
    for name in _PLACEHOLDER.findall(candidate):
        if name not in PATH_PLACEHOLDERS:
            raise _fail(
                "The path for %s uses the unknown placeholder {%s}." % (key, name),
                key=key,
                hint="Only {request_id} and {model} may appear in a path.",
            )
    stripped = _PLACEHOLDER.sub("", candidate)
    if "{" in stripped or "}" in stripped:
        raise _fail(
            "The path for %s has an unbalanced brace." % key,
            key=key,
        )
    return candidate


def _clean_method(value: Any, key: str) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().upper()
    if text not in HTTP_METHODS:
        raise _fail(
            "The method for %s must be one of %s." % (key, ", ".join(HTTP_METHODS)),
            key=key,
        )
    return text


def _clean_text(value: Any, key: str, *, limit: int = 200) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise _fail("The value for %s must be text." % key, key=key)
    text = value.strip()
    if len(text) > limit:
        raise _fail("The value for %s is too long." % key, key=key)
    return text


def _clean_field(value: Any, key: str) -> str:
    """A field name, or a dotted path into the provider payload."""
    text = _clean_text(value, key)
    if not text:
        return ""
    if not _FIELD_NAME.match(text):
        raise _fail(
            "The field name for %s must look like a JSON field, optionally dotted." % key,
            key=key,
            hint="For example 'data.0.url' or 'status'."
            ,
        )
    return text


def _clean_words(value: Any, key: str, fallback: Sequence[str]) -> List[str]:
    if value in (None, "", []):
        return [str(item) for item in fallback]
    if isinstance(value, str):
        value = [part for part in re.split(r"[,\n]", value)]
    if not isinstance(value, (list, tuple)):
        raise _fail("The value for %s must be a list of words." % key, key=key)
    words: List[str] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, (str, int, float)):
            raise _fail("Every entry in %s must be text." % key, key=key)
        text = str(entry).strip().lower()
        if text:
            words.append(text)
        if len(words) > 40:
            raise _fail("The list for %s is too long." % key, key=key)
    return words or [str(item) for item in fallback]


def _clean_scalar_map(value: Any, key: str) -> Dict[str, Any]:
    """A flat map of static fields. No nesting, so nothing here can become a payload tree."""
    if value in (None, "", {}):
        return {}
    if not isinstance(value, Mapping):
        raise _fail("The value for %s must be an object." % key, key=key)
    cleaned: Dict[str, Any] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not _JSON_KEY.match(name):
            raise _fail("The key %r in %s is not a usable field name." % (name, key), key=key)
        if isinstance(item, (dict, list, tuple)):
            raise _fail(
                "The value of %s.%s must be text, a number, or true/false." % (key, name),
                key=key,
            )
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise _fail(
                "The value of %s.%s must be text, a number, or true/false." % (key, name),
                key=key,
            )
        cleaned[name] = item
        if len(cleaned) > 32:
            raise _fail("The object for %s has too many entries." % key, key=key)
    return cleaned


def _clean_headers(value: Any, key: str) -> Dict[str, str]:
    raw = _clean_scalar_map(value, key)
    # Every header name the platform itself sets or that would let a mapping
    # smuggle a credential or a framing decision past the sealed store. `cookie`
    # and `proxy-authorization` belong here for the same reason `authorization`
    # does: they carry credentials, and credentials go through the sealed field.
    reserved = {
        "authorization",
        "proxy-authorization",
        "cookie",
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "accept-encoding",
        "connection",
    }
    cleaned: Dict[str, str] = {}
    for name, item in raw.items():
        if name.lower() in reserved:
            raise _fail(
                "The header %s cannot be set here; the platform manages it." % name,
                key=key,
            )
        cleaned[name] = "" if item is None else str(item)
    return cleaned


def _clean_references(value: Any, key: str) -> List[Dict[str, Any]]:
    """Which body field carries which kind of reference image."""
    if value in (None, "", []):
        return []
    if not isinstance(value, (list, tuple)):
        raise _fail("The value for %s must be a list." % key, key=key)
    if len(value) > 12:
        raise _fail("At most 12 reference mappings may be declared.", key=key)
    cleaned: List[Dict[str, Any]] = []
    for index, entry in enumerate(value):
        where = "%s[%d]" % (key, index)
        if not isinstance(entry, Mapping):
            raise _fail("Every reference mapping must be an object.", key=where)
        field = _clean_field(entry.get("field"), where + ".field")
        if not field:
            raise _fail("A reference mapping needs a field to send the image in.", key=where)
        role = _clean_text(entry.get("role"), where + ".role", limit=40) or "main_subject"
        encoding = (_clean_text(entry.get("encoding"), where + ".encoding", limit=20) or "url")
        encoding = encoding.lower()
        if encoding not in REFERENCE_ENCODINGS:
            raise _fail(
                "The encoding for %s must be one of %s."
                % (where, ", ".join(REFERENCE_ENCODINGS)),
                key=where,
            )
        cleaned.append({"field": field, "role": role, "encoding": encoding})
    return cleaned


#: Maps whose keys are chosen by the user rather than by this module. They are
#: passed through the allowlist untouched and validated by their own cleaner,
#: which enforces the real rules: flat scalars only, a 32-entry cap, and a
#: reserved-header list. Walking them against an empty allowlist, as this used
#: to, refused every entry with "not recognised" -- which blocked both the 32
#: cap and the reserved-header message from ever being reached.
_OPEN_MAPS = ("submit.extra_fields", "submit.extra_headers")


def _merge_block(base: Any, raw: Any, key: str) -> Any:
    """Walk the allowlist. An unknown key is a save-time error, not a surprise later."""
    for suffix in _OPEN_MAPS:
        if key.endswith("." + suffix):
            return raw
    if isinstance(base, Mapping):
        if raw in (None, "", {}):
            return {name: _merge_block(item, None, "%s.%s" % (key, name)) for name, item in base.items()}
        if not isinstance(raw, Mapping):
            raise _fail("The value for %s must be an object." % key, key=key)
        unknown = sorted(name for name in raw if name not in base)
        if unknown:
            raise _fail(
                "The mapping setting %s.%s is not recognised." % (key, unknown[0]),
                key="%s.%s" % (key, unknown[0]),
                hint="Allowed here: %s." % ", ".join(sorted(base)),
            )
        merged: Dict[str, Any] = {}
        for name, item in base.items():
            merged[name] = _merge_block(item, raw.get(name), "%s.%s" % (key, name))
        return merged
    if isinstance(base, list):
        # A list value is handed to the block that owns it -- reference mappings,
        # status vocabularies, error paths -- and validated there. Returning an
        # empty list, as this used to, silently discarded every declared
        # reference: a wrong mapping then produced an image with no source image
        # instead of an error, which is the one failure mode this module exists
        # to prevent.
        if raw in (None, "", {}):
            return []
        return list(raw) if isinstance(raw, (list, tuple)) else raw
    if isinstance(base, bool):
        if raw is None:
            return base
        if not isinstance(raw, bool):
            raise _fail("The value for %s must be true or false." % key, key=key)
        return raw
    if isinstance(base, (int, float)):
        if raw is None or raw == "":
            return base
        if isinstance(raw, bool):
            raise _fail("The value for %s must be a number." % key, key=key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise _fail("The value for %s must be a number." % key, key=key) from None
    return "" if raw is None else _clean_text(raw, key, limit=500)


def load_spec(raw: Any) -> Dict[str, Any]:
    """Normalise and validate a stored mapping. Raises before anything is sent."""
    if raw in (None, "", {}):
        raw = {}
    if isinstance(raw, str):
        raw = json_loads(raw, {})
    if not isinstance(raw, Mapping):
        raise _fail("A provider mapping must be an object.", key="mapping")

    spec = _merge_block(_blank_spec(), raw, "mapping")

    structure = str(spec.get("structure") or "queued").strip().lower()
    if structure not in STRUCTURE_TYPES:
        raise _fail(
            "The provider structure must be one of %s." % ", ".join(STRUCTURE_TYPES),
            key="mapping.structure",
        )
    spec["structure"] = structure

    for block, name in _PATH_KEYS:
        spec[block][name] = _clean_path(spec[block].get(name), "mapping.%s.%s" % (block, name))
    for block, name in _METHOD_KEYS:
        method = _clean_method(spec[block].get(name), "mapping.%s.%s" % (block, name))
        if not method:
            method = "GET" if block in ("status", "result", "models", "test") else "POST"
        spec[block][name] = method

    submit = spec["submit"]
    for name in (
        "model_field",
        "prompt_field",
        "negative_prompt_field",
        "prompt_instructions_field",
        "count_field",
        "seed_field",
        "mask_field",
        "parameters_field",
    ):
        submit[name] = _clean_field(submit.get(name), "mapping.submit.%s" % name)
    body_type = (_clean_text(submit.get("body_type"), "mapping.submit.body_type", limit=20) or "json")
    body_type = body_type.lower()
    if body_type not in SUBMIT_BODY_TYPES:
        raise _fail(
            "The submit body type must be one of %s." % ", ".join(SUBMIT_BODY_TYPES),
            key="mapping.submit.body_type",
        )
    submit["body_type"] = body_type
    submit["extra_fields"] = _clean_scalar_map(
        submit.get("extra_fields"), "mapping.submit.extra_fields"
    )
    submit["extra_headers"] = _clean_headers(
        submit.get("extra_headers"), "mapping.submit.extra_headers"
    )
    submit["references"] = _clean_references(
        submit.get("references"), "mapping.submit.references"
    )

    status = spec["status"]
    for name in (
        "request_id_path",
        "status_path",
        "progress_path",
        "message_path",
        "queue_position_path",
        "retry_after_path",
    ):
        status[name] = _clean_field(status.get(name), "mapping.status.%s" % name)
    try:
        status["poll_after_seconds"] = max(0.2, min(float(status.get("poll_after_seconds") or 2.0), 60.0))
    except (TypeError, ValueError):
        status["poll_after_seconds"] = DEFAULT_POLL_SECONDS
    status["succeeded_values"] = _clean_words(
        status.get("succeeded_values"), "mapping.status.succeeded_values", DEFAULT_SUCCEEDED
    )
    status["failed_values"] = _clean_words(
        status.get("failed_values"), "mapping.status.failed_values", DEFAULT_FAILED
    )
    status["cancelled_values"] = _clean_words(
        status.get("cancelled_values"), "mapping.status.cancelled_values", DEFAULT_CANCELLED
    )
    status["processing_values"] = _clean_words(
        status.get("processing_values"), "mapping.status.processing_values", DEFAULT_PROCESSING
    )

    result = spec["result"]
    for name in (
        "outputs_path",
        "output_url_path",
        "output_type_path",
        "output_width_path",
        "output_height_path",
        "output_duration_path",
        "output_seed_path",
        "id_path",
        "result_ready_path",
    ):
        result[name] = _clean_field(result.get(name), "mapping.result.%s" % name)

    response = spec["response"]
    response_type = (_clean_text(response.get("type"), "mapping.response.type", limit=20) or "json")
    response_type = response_type.lower()
    if response_type not in RESPONSE_TYPES:
        raise _fail(
            "The response type must be json or binary.",
            key="mapping.response.type",
        )
    response["type"] = response_type
    for name in ("outputs_path", "output_url_path", "output_type_path"):
        response[name] = _clean_field(response.get(name), "mapping.response.%s" % name)
    response["binary_content_type"] = _clean_text(
        response.get("binary_content_type"), "mapping.response.binary_content_type", limit=100
    )

    models = spec["models"]
    for name in ("ids_path", "id_path", "name_path"):
        models[name] = _clean_field(models.get(name), "mapping.models.%s" % name)

    errors = spec["errors"]
    paths = errors.get("message_paths")
    if isinstance(paths, str):
        paths = [part.strip() for part in paths.split(",")]
    if not isinstance(paths, (list, tuple)) or not paths:
        paths = list(DEFAULT_SPEC["errors"]["message_paths"])
    errors["message_paths"] = [
        _clean_field(item, "mapping.errors.message_paths") for item in paths[:8]
    ]

    _require_for_structure(spec)
    return spec


def _require_for_structure(spec: Mapping[str, Any]) -> None:
    """Refuse a mapping that is missing the paths its structure cannot work without."""
    structure = spec["structure"]
    for block, name in _REQUIRED_BY_STRUCTURE[structure]:
        if not spec[block].get(name):
            raise _fail(
                "A %s provider needs a %s path." % (structure, block),
                key="mapping.%s.%s" % (block, name),
                hint="Add the URL this platform should call for the %s step." % block,
            )
    if structure == "sync" and spec["response"]["type"] == "json":
        if not spec["response"].get("outputs_path"):
            raise _fail(
                "A synchronous provider must say where the output lives in the response.",
                key="mapping.response.outputs_path",
                hint="For example data, or data.images.",
            )
    if structure == "queued" and not spec["status"].get("status_path"):
        raise _fail(
            "A queued provider must say which field carries the status word.",
            key="mapping.status.status_path",
        )


def spec_summary(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """What the connection screen shows a user about their own mapping."""
    return {
        "structure": spec.get("structure"),
        "submit_path": spec["submit"].get("path"),
        "status_path": spec["status"].get("path"),
        "result_path": spec["result"].get("path"),
        "prompt_field": spec["submit"].get("prompt_field"),
        "model_field": spec["submit"].get("model_field"),
        "outputs_path": (
            spec["response"].get("outputs_path")
            or spec["result"].get("outputs_path")
        ),
        "output_url_path": (
            spec["response"].get("output_url_path")
            or spec["result"].get("output_url_path")
        ),
        "reference_fields": [item["field"] for item in spec["submit"].get("references") or []],
        "mask_field": spec["submit"].get("mask_field"),
        "response_type": spec["response"].get("type"),
    }


# ---------------------------------------------------------------------------
# Presets for the two shapes this platform knows how to describe out of the box
# ---------------------------------------------------------------------------

#: The OpenAI images API. Kept here rather than in openai.py because it is the
#: canonical worked example a user reads when writing their own mapping.
OPENAI_IMAGES_SPEC: Dict[str, Any] = {
    "structure": "openai_images",
    "submit": {
        "path": "/v1/images/generations",
        "method": "POST",
        "body_type": "json",
        "model_field": "model",
        "prompt_field": "prompt",
        "count_field": "n",
        "extra_fields": {},
        "references": [],
    },
    "response": {
        "type": "json",
        "outputs_path": "data",
        "output_url_path": "url",
    },
    "models": {"path": "/v1/models", "ids_path": "data", "id_path": "id", "name_path": "id"},
    "test": {"path": "/v1/models", "method": "GET"},
}

#: The edit endpoint. A distinct mapping because it is multipart, not JSON.
OPENAI_IMAGES_EDIT_SPEC: Dict[str, Any] = {
    "structure": "openai_images",
    "submit": {
        "path": "/v1/images/edits",
        "method": "POST",
        "body_type": "multipart",
        "model_field": "model",
        "prompt_field": "prompt",
        "count_field": "n",
        "mask_field": "mask",
        "references": [{"field": "image", "role": "main_subject", "encoding": "multipart"}],
    },
    "response": {
        "type": "json",
        "outputs_path": "data",
        "output_url_path": "url",
    },
}


def preset_spec(name: str) -> Dict[str, Any]:
    presets = {
        "openai_images": OPENAI_IMAGES_SPEC,
        "openai_images_edit": OPENAI_IMAGES_EDIT_SPEC,
    }
    if name not in presets:
        raise _fail(
            "There is no mapping preset called that.",
            key="mapping.preset",
            hint="Known presets: %s." % ", ".join(sorted(presets)),
        )
    return load_spec(presets[name])


def render_path(template: str, *, request_id: str = "", model: str = "") -> str:
    """Substitute the two allowed placeholders with a plain string replace.

    There is no format specification, no attribute access and no call: the
    replacement is literal. A value containing braces therefore cannot reach the
    interpreter, and a mapping cannot express anything except these two facts.
    """
    text = template or ""
    if not text:
        return ""
    for name, value in (("request_id", request_id), ("model", model)):
        text = text.replace("{" + name + "}", value or "")
    return text


def _url_for(context: AdapterContext, path: str, **values: str) -> str:
    """Turn a mapping path into an absolute URL, or say precisely why it cannot."""
    rendered = render_path(path, **values)
    if not rendered:
        raise ApiError(
            CONFIGURATION_REQUIRED,
            "This connection has no path configured for that step.",
            details={"step": "mapping"},
            hint="Edit the connection and fill in the provider mapping.",
        )
    if rendered.startswith("https://") or rendered.startswith("http://"):
        return rendered
    base = (context.base_url or "").strip()
    if not base:
        raise ApiError(
            CONFIGURATION_REQUIRED,
            "This connection has no base URL and the step path is not a full URL.",
            hint="Set a base URL on the connection, or use an absolute https URL.",
        )
    return join_url(base, rendered)


def spec_supports_cancel(spec: Any) -> bool:
    """Whether this mapping declares a cancel call. The per-connection truth."""
    try:
        loaded = load_spec(spec)
    except ApiError:
        return False
    return bool(loaded["cancel"].get("path"))


def _reference_items(request: GenerationRequest) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry in request.references or ():
        if isinstance(entry, Mapping):
            items.append(dict(entry))
        elif isinstance(entry, str):
            items.append({"url": entry})
    return items


def _match_reference(entries: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    """Pick the reference that fills a declared field.

    A declared field either names the role it wants or takes the next unclaimed
    reference in order. The second case is what makes a single-image edit endpoint
    usable without the user having to think about roles.
    """
    if not entries:
        return None
    if role:
        for index, item in enumerate(entries):
            if str(item.get("role") or "") == role:
                return entries.pop(index)
    return entries.pop(0)


def _reference_url(context: AdapterContext, item: Mapping[str, Any]) -> str:
    url = item.get("url") or item.get("asset_url") or item.get("src")
    if url:
        return str(url)
    path = item.get("path") or item.get("local_path")
    if not path:
        return ""
    base = (context.base_url or "").strip().rstrip("/")
    return base + "/" + str(path).lstrip("/")


def _fetch_reference(context: AdapterContext, url: str) -> Tuple[bytes, str]:
    """Read a reference image this server is about to inline into a request body.

    This is the only place a mapping causes a second URL to be read, and it goes
    through the same guarded client as everything else: a private or metadata
    address is refused here for exactly the same reasons it is refused anywhere.
    """
    client = context.http
    if client is None:
        raise ApiError(INTERNAL, "The adapter has no HTTP client.")
    response = client.request(
        "GET",
        url,
        accept="image/*,*/*",
        max_bytes=MAX_INLINE_BYTES,
        follow_redirects=False,
    )
    if response.status >= 400:
        raise ApiError(
            INVALID_RESPONSE,
            "A reference image could not be read for this request.",
            details={"status": response.status},
            hint="The reference must be reachable over https from this deployment.",
        )
    head = response.body[:16]
    if b"<" == head[:1] and b"html" in response.body[:200].lower():
        raise ApiError(
            INVALID_RESPONSE,
            "A reference image URL returned a web page rather than an image.",
            hint="Use a direct link to the image file.",
        )
    content_type = (response.headers.get("content-type") or "image/png").split(";")[0].strip()
    return response.body, content_type


def _multipart_body(
    fields: Sequence[Tuple[str, str]],
    files: Sequence[Tuple[str, str, str, bytes]],
) -> Tuple[bytes, str]:
    """A minimal multipart body, built here rather than by a dependency."""
    boundary = "----studiomapping7f31c0d5a9e24b6d"
    chunks: List[bytes] = []
    for name, value in fields:
        chunks.append(("--" + boundary + "\r\n").encode("utf-8"))
        chunks.append((
            'Content-Disposition: form-data; name="' + name + '"\r\n\r\n'
        ).encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, file_name, content_type, payload in files:
        chunks.append(("--" + boundary + "\r\n").encode("utf-8"))
        chunks.append((
            'Content-Disposition: form-data; name="' + name
            + '"; filename="' + file_name + '"\r\n'
        ).encode("utf-8"))
        chunks.append(("Content-Type: " + content_type + "\r\n\r\n").encode("utf-8"))
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(("--" + boundary + "--\r\n").encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


def build_submit_request(
    spec: Mapping[str, Any],
    context: AdapterContext,
    request: GenerationRequest,
    model_key: str,
) -> Tuple[str, str, Optional[bytes], Dict[str, str], Dict[str, Any]]:
    """Return the method, URL, body, extra headers and a redaction-safe summary.

    Everything that goes out is a field this mapping named. Nothing is inferred.
    A parameter that no declared field claimed is reported in the summary as
    dropped rather than being appended to whichever field looked closest, because
    sending an unsupported field is at best ignored and at worst billed as a
    different request.
    """
    submit = spec["submit"]
    url = _url_for(context, submit.get("path") or "", model=model_key)
    method = submit.get("method") or "POST"
    parameters = dict(request.parameters or {})
    used: List[str] = []

    def take(field: str) -> Any:
        if not field:
            return None
        if field in parameters:
            used.append(field)
            return parameters.pop(field)
        return None

    values: Dict[str, Any] = {}
    prompt_field = submit.get("prompt_field") or ""
    if prompt_field:
        values[prompt_field] = request.composed_prompt()
    if request.negative_prompt and submit.get("negative_prompt_field"):
        values[submit["negative_prompt_field"]] = request.negative_prompt
    if request.prompt_instructions and submit.get("prompt_instructions_field"):
        values[submit["prompt_instructions_field"]] = request.composed_prompt()

    count_field = submit.get("count_field") or ""
    count = take(count_field)
    if count is not None:
        values[count_field] = count
    elif count_field and request.count and request.count > 1:
        values[count_field] = request.count

    seed_field = submit.get("seed_field") or ""
    seed = take(seed_field)
    if seed is None and request.seed is not None:
        seed = request.seed
    if seed is not None and seed_field:
        values[seed_field] = seed

    parameters_field = submit.get("parameters_field") or ""
    if parameters_field and parameters:
        nested = {name: parameters.pop(name) for name in list(parameters)}
        used.extend(nested)
        values[parameters_field] = nested

    # A declared field may itself name a parameter, which is how a provider that
    # takes a differently-named field is described without a code change.
    for key in (
        "model_field",
        "prompt_field",
        "negative_prompt_field",
        "prompt_instructions_field",
        "count_field",
        "seed_field",
    ):
        field = submit.get(key) or ""
        if not field:
            continue
        chosen = take(field)
        if chosen is not None:
            values[field] = chosen

    model_field = submit.get("model_field") or ""
    if model_field:
        values.setdefault(model_field, model_key)

    for name, value in (submit.get("extra_fields") or {}).items():
        if value in (None, ""):
            continue
        values.setdefault(name, value)
        used.append(name)

    for name, value in list(parameters.items()):
        if name in values:
            values[name] = value
            used.append(name)
            parameters.pop(name)

    summary = {
        "prompt_chars": len(request.composed_prompt()),
        "fields_sent": sorted(str(name) for name in values),
        "parameters_used": sorted(set(used)),
        "parameters_dropped": sorted(parameters),
    }

    extra_headers = dict(submit.get("extra_headers") or {})
    body_type = submit.get("body_type") or "json"
    mask_field = submit.get("mask_field") or ""
    mask_url = request.mask_url or ""
    references = _reference_items(request)
    declared = [dict(item) for item in submit.get("references") or []]

    inlined = 0
    files: List[Tuple[str, str, str, bytes]] = []
    for entry in declared:
        chosen = _match_reference(references, str(entry.get("role") or ""))
        if not chosen:
            continue
        target = _reference_url(context, chosen)
        if not target:
            continue
        field = entry["field"]
        encoding = entry.get("encoding") or "url"
        if encoding == "url":
            existing = values.get(field)
            if existing is None:
                values[field] = target
            elif isinstance(existing, list):
                existing.append(target)
            else:
                values[field] = [existing, target]
            continue
        payload, content_type = _fetch_reference(context, target)
        inlined += 1
        if encoding == "base64":
            values[field] = "data:" + content_type + ";base64," + base64.b64encode(payload).decode("ascii")
        else:
            file_name = os.path.basename(target.split("?")[0]) or "reference.png"
            files.append((field, file_name, content_type, payload))

    if mask_url and mask_field:
        payload, content_type = _fetch_reference(context, mask_url)
        inlined += 1
        if body_type == "json":
            values[mask_field] = mask_url
        elif body_type == "base64":
            values[mask_field] = "data:" + content_type + ";base64," + base64.b64encode(payload).decode("ascii")
        else:
            files.append((mask_field, "mask.png", content_type, payload))

    summary["references_inlined"] = inlined

    if body_type == "json":
        summary["body"] = "json"
        summary["content_type"] = "application/json"
        return method, url, json_dumps(drop_empty(values)).encode("utf-8"), extra_headers, summary

    fields: List[Tuple[str, str]] = []
    for name, value in values.items():
        if isinstance(value, (dict, list)):
            fields.append((name, json_dumps(value)))
        elif value is not None:
            fields.append((name, str(value)))
    payload_bytes, content_type = _multipart_body(fields, files)
    summary["body"] = "multipart"
    summary["content_type"] = content_type
    return method, url, payload_bytes, extra_headers, summary
# ---------------------------------------------------------------------------
# Reading a provider answer
#
# Everything below reads a field the mapping named. Nothing searches a payload
# for something that merely looks like a result, because the alternative to a
# loud failure is showing a user a file the provider never made.
# ---------------------------------------------------------------------------


def _read(data: Any, path: Any, default: Any = None) -> Any:
    """get_path, except that an empty path means no value, not the whole payload."""
    if not path:
        return default
    return get_path(data, path, default)


def _word_set(values: Any, fallback: Sequence[str]) -> frozenset:
    """A normalised set of status words, or the documented defaults when empty."""
    out = set()
    for item in values or ():
        text = str(item or "").strip().lower()
        if text:
            out.add(text)
    return frozenset(out) if out else frozenset(fallback)


def classify_status(word: Any, spec: Mapping[str, Any]) -> str:
    """Translate a provider status word into one of our job states.

    A word matching none of the four lists is reported as processing. Guessing
    upwards is the one mistake here that both costs money and shows the user a
    result that does not exist, so an unrecognised word never means completed.
    """
    status = spec.get("status") or {}
    text = str(word or "").strip().lower()
    if not text:
        return STATE_PROCESSING
    if text in _word_set(status.get("succeeded_values"), DEFAULT_SUCCEEDED):
        return STATE_COMPLETED
    if text in _word_set(status.get("failed_values"), DEFAULT_FAILED):
        return STATE_FAILED
    if text in _word_set(status.get("cancelled_values"), DEFAULT_CANCELLED):
        return STATE_CANCELLED
    return STATE_PROCESSING


def _wants_binary(spec: Mapping[str, Any]) -> bool:
    """Whether the mapping says the provider answers with a file rather than JSON."""
    response = spec.get("response") or {}
    return str(response.get("type") or "json").strip().lower() == "binary"


def _payload_of(response: Any, spec: Mapping[str, Any]) -> Any:
    """The response body as the mapping describes it: raw bytes, or parsed JSON."""
    if _wants_binary(spec):
        return response.body
    return response.json()


_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
}


def _extension_for(content_type: str) -> str:
    """A file extension for a content type, or .bin when the type is unknown."""
    bare = str(content_type or "").split(";")[0].strip().lower()
    return _EXTENSIONS.get(bare, ".bin")


def _output_directory(context: AdapterContext) -> str:
    """Where an inline or binary result is staged before the runner stores it."""
    configured = ""
    if isinstance(context.config, Mapping):
        configured = str(context.config.get("output_dir") or "")
    directory = configured or os.path.join(tempfile.gettempdir(), "studio-declarative-output")
    os.makedirs(directory, exist_ok=True)
    return directory


def _store_binary(context: AdapterContext, data: bytes, content_type: str = "") -> ProviderOutput:
    """Write bytes the provider returned inline, and report them honestly.

    The file is staged under the temporary output directory, not in object
    storage: the runner imports it into the workspace asset store and records
    the real key there. The url field stays empty so nothing downstream can
    mistake a staging path for a durable address.
    """
    if not data:
        raise ApiError(
            INVALID_RESPONSE,
            "The provider answered with an empty body where the generated file should have been.",
        )
    if len(data) > MAX_BINARY_BYTES:
        raise ApiError(
            INVALID_RESPONSE,
            "The provider sent a file larger than this deployment will accept.",
            details={"bytes": len(data), "limit": MAX_BINARY_BYTES},
        )
    directory = _output_directory(context)
    digest = hashlib.sha256(data).hexdigest()[:20]
    path = os.path.join(directory, "declarative-" + digest + _extension_for(content_type))
    part = path + ".part"
    try:
        with open(part, "wb") as handle:
            handle.write(data)
        os.replace(part, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            os.remove(part)
        raise ApiError(
            STORAGE_FAILED,
            "The generated file could not be staged on this server.",
            provider_message=str(error),
        ) from error
    return ProviderOutput(
        url="",
        content_type=content_type or None,
        file_name=os.path.basename(path),
        metadata={"local_path": path, "byte_size": len(data), "source": "response body"},
    )


def _from_inline(
    context: AdapterContext, encoded: str, response_spec: Mapping[str, Any]
) -> ProviderOutput:
    """Decode base64 image data a provider returned instead of a link."""
    content_type = str(response_spec.get("binary_content_type") or "image/png")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ApiError(
            INVALID_RESPONSE,
            "The provider returned inline image data that is not valid base64.",
        ) from None
    return _store_binary(context, raw, content_type)


def _output_from_item(
    context: AdapterContext,
    item: Any,
    block_spec: Mapping[str, Any],
    response_spec: Mapping[str, Any],
    url_path: Any,
) -> Optional[ProviderOutput]:
    """One element of a declared output list, or None when it carries no file."""
    if isinstance(item, str):
        if item.startswith("http://") or item.startswith("https://"):
            return ProviderOutput(url=item)
        return None
    if not isinstance(item, Mapping):
        return None
    url = ""
    candidates: Tuple[str, ...] = ((str(url_path),) if url_path else ()) + tuple(URL_KEYS)
    for candidate in candidates:
        value = item.get(candidate)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            url = value
            break
    if not url:
        inline = item.get("b64_json")
        if isinstance(inline, str) and inline:
            return _from_inline(context, inline, response_spec)
        return None
    return ProviderOutput(
        url=url,
        content_type=_read(item, block_spec.get("output_type_path")) or None,
        width=to_int(_read(item, block_spec.get("output_width_path"))),
        height=to_int(_read(item, block_spec.get("output_height_path"))),
        duration_seconds=to_float(_read(item, block_spec.get("output_duration_path"))),
        seed=to_int(_read(item, block_spec.get("output_seed_path"))),
        provider_asset_id=_read(item, block_spec.get("id_path")) or None,
    )


def _outputs_from(
    context: AdapterContext, payload: Any, spec: Mapping[str, Any], block: str
) -> Tuple[ProviderOutput, ...]:
    """Every output the mapping declared in this payload, and nothing else."""
    if _wants_binary(spec):
        if not isinstance(payload, (bytes, bytearray)):
            raise ApiError(
                INVALID_RESPONSE,
                "The mapping says this provider answers with a file, but the answer was not a file body.",
            )
        stated = str((spec.get("response") or {}).get("binary_content_type") or "")
        return (_store_binary(context, bytes(payload), stated),)
    if not isinstance(payload, Mapping):
        raise ApiError(
            INVALID_RESPONSE,
            "The provider answered with something other than a JSON object.",
            details={"received": type(payload).__name__},
            hint="Check that the base URL points at the API rather than at a web page.",
        )
    block_spec = spec.get(block) or {}
    response_spec = spec.get("response") or {}
    outputs_path = block_spec.get("outputs_path") or response_spec.get("outputs_path")
    url_path = block_spec.get("output_url_path") or response_spec.get("output_url_path")
    if not outputs_path:
        raise ApiError(
            INVALID_RESPONSE,
            "This mapping does not say where the output appears in the response.",
            details={"block": block},
            hint="Set response.outputs_path, for example data.",
        )
    found = _read(payload, outputs_path)
    items = as_list(found) if found is not None else []
    out: List[ProviderOutput] = []
    for item in items:
        output = _output_from_item(context, item, block_spec, response_spec, url_path)
        if output is not None:
            out.append(output)
    return tuple(out)


def _status_word(payload: Any, spec: Mapping[str, Any]) -> str:
    """The provider status word, from the field the mapping named."""
    status = spec.get("status") or {}
    value = _read(payload, status.get("status_path"))
    if value is None or value == "":
        if isinstance(payload, Mapping):
            value = first_string(payload, STATUS_KEYS)
    return str(value or "").strip()


def _outcome_for(code: str) -> str:
    """Coerce a diagnostic code into the fixed list the wizard can render.

    The wizard has no entry for "this connection is not configured yet", so a
    mapping problem is reported as unsupported_operation and the message says
    exactly which setting is missing. A generic outcome would be worse.
    """
    return code if code in CONNECTION_TEST_OUTCOMES else UNSUPPORTED_OPERATION


@register
class DeclarativeHttpAdapter(ModelAdapter):
    """A provider of the user own choosing, driven entirely by their mapping.

    Nothing here executes user text. The mapping is data: paths, field names,
    status words and a two-name placeholder list. A wrong mapping produces a
    clear failure, never a plausible-looking wrong image.
    """

    slug = "declarative_http"
    label = "Custom HTTP provider"
    summary = "Any image or video API, described by a mapping you write. No mapping code is ever run."
    is_mock = False
    auth_schemes = ("bearer", "header", "query", "none")
    discovers_models = True
    #: Cancelling depends on the individual mapping, so the class cannot promise
    #: it. Call cancel_supported() with the connection context for the real
    #: answer, and show the cancel button on that.
    supports_cancel = False

    def default_modes(self) -> Tuple[str, ...]:
        """The narrowest honest default: generate an image from text.

        Anything else a hand-written mapping claims has to be typed in by the
        user, because the platform cannot read a private API documentation.
        """
        return ("text_to_image",)

    # -- the mapping this connection carries ---------------------------------

    def spec_for(self, context: AdapterContext) -> Dict[str, Any]:
        """This connection stored mapping, validated. Raises before anything is sent."""
        raw: Any = {}
        if isinstance(context.config, Mapping):
            raw = context.config.get("mapping") or {}
        return load_spec(raw)

    def cancel_supported(self, context: AdapterContext) -> bool:
        """Whether this particular mapping declares a cancel call."""
        try:
            return spec_supports_cancel(self.spec_for(context))
        except ApiError:
            return False

    def describe(self, context: AdapterContext) -> Dict[str, Any]:
        """What the connection screen shows the user about their own mapping."""
        return spec_summary(self.spec_for(context))

    def capabilities_for(
        self, context: AdapterContext, model_key: str
    ) -> Optional[CapabilityProfile]:
        """Always None: an arbitrary endpoint publishes no capability metadata.

        Returning a guess here would switch on studio buttons this provider may
        not implement, so the platform uses exactly what the user declared.
        """
        return None

    # -- the operations -------------------------------------------------------

    def test_connection(self, context: AdapterContext) -> ConnectionTestResult:
        """Make the one call the mapping named for a test, and report what happened.

        Only an answer from the provider produces ok=True. A mapping with no
        test path is reported as a configuration gap rather than silently
        treated as a pass, because the wizard would otherwise imply the key was
        verified when nothing was contacted.
        """
        started = time.monotonic()
        try:
            spec = self.spec_for(context)
        except ApiError as error:
            return ConnectionTestResult(
                outcome=_outcome_for(error.code),
                ok=False,
                message=error.message,
                detail={"field": error.field or "", "mapping": True},
            )
        test = spec.get("test") or {}
        models = spec.get("models") or {}
        path = test.get("path") or models.get("path")
        if not path:
            return ConnectionTestResult(
                outcome=UNSUPPORTED_OPERATION,
                ok=False,
                message="This mapping names no test or model-list path, so no request was made.",
                detail={
                    "mapping": True,
                    "hint": "Add a test path or a models path, then test again. Until then the key is unverified.",
                },
            )
        try:
            url = _url_for(context, path)
            response = self._request(context, test.get("method") or "GET", url)
        except ApiError as error:
            latency = int((time.monotonic() - started) * 1000)
            detail: Dict[str, Any] = {"mapping": False}
            if isinstance(error.details, Mapping):
                detail.update(dict(error.details))
            return ConnectionTestResult(
                outcome=_outcome_for(error.code),
                ok=False,
                http_status=error.status,
                latency_ms=latency,
                message=error.message,
                detail=detail,
            )
        latency = int((time.monotonic() - started) * 1000)
        seen: Tuple[str, ...] = ()
        if models.get("path"):
            try:
                listed = _payload_of(response, spec)
                found = _read(listed, models.get("ids_path"))
                entries = as_list(found) if found is not None else []
                keys: List[str] = []
                for entry in entries[:20]:
                    if isinstance(entry, str):
                        keys.append(entry)
                    elif isinstance(entry, Mapping):
                        key = _read(entry, models.get("id_path")) or first_string(entry, ID_KEYS)
                        if key:
                            keys.append(str(key))
                seen = tuple(unique(keys))
            except ApiError:
                seen = ()
        return ConnectionTestResult(
            outcome="",
            ok=True,
            http_status=response.status,
            latency_ms=latency,
            message=(
                "The provider answered HTTP %d. This proves the endpoint and the credential work; "
                "it does not prove every model identifier in the mapping is right." % response.status
            ),
            detail={"path": path, "models_seen": len(seen)},
            models_seen=seen,
        )
    def list_models(self, context: AdapterContext) -> Tuple[DiscoveredModel, ...]:
        """Read the model list the mapping named, if it named one."""
        spec = self.spec_for(context)
        models = spec.get("models") or {}
        path = models.get("path")
        if not path:
            raise ApiError(
                UNSUPPORTED_OPERATION,
                "This connection mapping does not describe a model list.",
                details={"adapter": self.slug},
                hint="Type the model identifier from the provider documentation, or add a models path to the mapping.",
            )
        payload = self._json(context, models.get("method") or "GET", _url_for(context, path))
        found = _read(payload, models.get("ids_path"))
        entries = as_list(found) if found is not None else as_list(payload)
        out: List[DiscoveredModel] = []
        for entry in entries:
            if isinstance(entry, str):
                out.append(DiscoveredModel(model_key=entry, display_name=entry, modality="other"))
            elif isinstance(entry, Mapping):
                key = _read(entry, models.get("id_path")) or first_string(entry, ID_KEYS)
                if not key:
                    continue
                name = _read(entry, models.get("name_path")) or ""
                # The provider stated no modality, so none is claimed here. The
                # user declares it when registering the model.
                out.append(
                    DiscoveredModel(
                        model_key=str(key),
                        display_name=str(name) or str(key),
                        modality="other",
                    )
                )
        return tuple(out)

    def submit(self, context: AdapterContext, request: GenerationRequest) -> SubmittedJob:
        """Send the request the mapping describes. This is the call that can cost money."""
        spec = self.spec_for(context)
        method, url, body, extra_headers, summary = build_submit_request(
            spec, context, request, request.model_key
        )
        headers = dict(extra_headers)
        declared_type = str(summary.get("content_type") or "")
        if declared_type and not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = declared_type
        structure = spec["structure"]
        response = self._request(context, method, url, body=body, headers=headers)
        payload = _payload_of(response, spec)

        if structure in ("sync", "openai_images"):
            outputs = _outputs_from(context, payload, spec, "response")
            if not outputs:
                raise ApiError(
                    INVALID_RESPONSE,
                    "The provider answered, but nothing in the answer matched the output path this mapping declares.",
                    details={
                        "structure": structure,
                        "outputs_path": (spec.get("response") or {}).get("outputs_path"),
                    },
                    hint="Compare the mapping response paths with a real response from this provider.",
                )
            return SubmittedJob(
                provider_job_id="",
                outputs=outputs,
                raw_status=STATE_COMPLETED,
                poll_after_seconds=0.0,
            )

        if not isinstance(payload, Mapping):
            raise ApiError(
                INVALID_RESPONSE,
                "The provider accepted the request but answered with something other than a JSON object, so no job identifier could be read.",
                details={"structure": structure},
            )

        request_id = ""
        response_spec = spec.get("response") or {}
        if response_spec.get("outputs_path"):
            candidates = _read(payload, response_spec.get("outputs_path"))
            first_entry = as_list(candidates)[0] if candidates is not None else None
            if isinstance(first_entry, Mapping):
                request_id = str(_read(first_entry, response_spec.get("id_path")) or "")
            elif isinstance(first_entry, str):
                request_id = first_entry
        if not request_id:
            request_id = first_string(payload, ID_KEYS)
        if not request_id:
            raise ApiError(
                INVALID_RESPONSE,
                "The provider accepted the request but returned no identifier, so this job cannot be tracked.",
                details={"structure": structure, "keys": sorted(payload.keys())[:12]},
                hint="Name the field that carries the request id in the mapping, under submit.",
            )

        status_spec = spec.get("status") or {}
        result_spec = spec.get("result") or {}
        submitted = SubmittedJob(
            provider_job_id=str(request_id),
            status_url=(
                _url_for(context, status_spec.get("path"), request_id=str(request_id), model=request.model_key)
                if status_spec.get("path")
                else ""
            ),
            result_url=(
                _url_for(context, result_spec.get("path"), request_id=str(request_id), model=request.model_key)
                if result_spec.get("path")
                else ""
            ),
            raw_status=_status_word(payload, spec) or STATE_QUEUED,
            poll_after_seconds=status_spec.get("poll_after_seconds"),
        )
        # A restarted worker rebuilds the status and result routes from these.
        submitted.model_key = request.model_key
        submitted.submit_summary = summary
        return submitted
    def poll(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        """Ask the provider what happened, and never upgrade an unclear answer."""
        spec = self.spec_for(context)
        structure = spec["structure"]
        if structure in ("sync", "openai_images"):
            return JobStatus(
                state=STATE_COMPLETED,
                provider_status=STATE_COMPLETED,
                message="The provider returned the finished output when the request was submitted.",
                outputs=job.outputs,
            )
        if structure == "upload":
            return self._poll_upload(context, spec, job)

        status_spec = spec.get("status") or {}
        status_url = job.status_url or _url_for(
            context,
            status_spec.get("path"),
            request_id=job.provider_job_id,
            model=getattr(job, "model_key", ""),
        )
        response = self._request(context, status_spec.get("method") or "GET", status_url)
        payload = _payload_of(response, spec)
        word = _status_word(payload, spec)
        state = classify_status(word, spec)
        progress = None
        if status_spec.get("progress_path"):
            progress = percent_to_fraction(_read(payload, status_spec.get("progress_path")))
        message = ""
        if status_spec.get("message_path"):
            message = str(_read(payload, status_spec.get("message_path")) or "")
        queue_position = to_int(_read(payload, status_spec.get("queue_position_path")))
        retry_after = to_float(_read(payload, status_spec.get("retry_after_path")))

        if state == STATE_COMPLETED:
            outputs = self._completed_outputs(context, spec, job, payload)
            return JobStatus(
                state=STATE_COMPLETED,
                progress=progress,
                provider_status=word or STATE_COMPLETED,
                message=message or "The provider reported this job complete.",
                outputs=outputs,
            )
        if state == STATE_FAILED:
            said = ""
            if isinstance(payload, Mapping):
                said = first_string(payload, MESSAGE_KEYS)
            return JobStatus(
                state=STATE_FAILED,
                progress=progress,
                provider_status=word or "failed",
                message=message or said or "The provider reported this job as failed.",
                error_code=PROVIDER_ERROR,
            )
        if state == STATE_CANCELLED:
            return JobStatus(
                state=STATE_CANCELLED,
                provider_status=word or "cancelled",
                message=message or "The provider reported this job cancelled.",
            )
        return JobStatus(
            state=state,
            progress=progress,
            provider_status=word or None,
            message=message or None,
            queue_position=queue_position,
            retry_after_seconds=retry_after,
        )

    def _poll_upload(
        self, context: AdapterContext, spec: Mapping[str, Any], job: SubmittedJob
    ) -> JobStatus:
        """Poll a provider that answers with the finished file on a follow-up GET.

        A 404 or an unreadable body here means the result is not ready yet, not
        that the job failed: the file route does not exist until it does.
        """
        result_spec = spec.get("result") or {}
        url = job.result_url or _url_for(
            context,
            result_spec.get("path"),
            request_id=job.provider_job_id,
            model=getattr(job, "model_key", ""),
        )
        try:
            response = self._request(context, result_spec.get("method") or "GET", url)
            outputs = _outputs_from(context, _payload_of(response, spec), spec, "result")
        except ApiError as error:
            if error.code in (ENDPOINT_NOT_FOUND, INVALID_RESPONSE):
                return JobStatus(
                    state=STATE_PROCESSING,
                    provider_status="waiting for result",
                    message="The result is not ready yet.",
                )
            raise
        if not outputs:
            return JobStatus(
                state=STATE_PROCESSING,
                provider_status="waiting for result",
                message="The result is not ready yet.",
            )
        return JobStatus(
            state=STATE_COMPLETED,
            provider_status=STATE_COMPLETED,
            message="The provider returned the finished output.",
            outputs=outputs,
        )

    def _completed_outputs(
        self,
        context: AdapterContext,
        spec: Mapping[str, Any],
        job: SubmittedJob,
        status_payload: Any,
    ) -> Tuple[ProviderOutput, ...]:
        """Read the finished outputs, from the status answer or the result route."""
        result_spec = spec.get("result") or {}
        outputs: Tuple[ProviderOutput, ...] = ()
        if result_spec.get("inline_in_status") and isinstance(status_payload, Mapping):
            try:
                outputs = _outputs_from(context, status_payload, spec, "result")
            except ApiError:
                outputs = ()
        if not outputs and result_spec.get("path"):
            url = job.result_url or _url_for(
                context,
                result_spec.get("path"),
                request_id=job.provider_job_id,
                model=getattr(job, "model_key", ""),
            )
            response = self._request(context, result_spec.get("method") or "GET", url)
            outputs = _outputs_from(context, _payload_of(response, spec), spec, "result")
        if not outputs:
            raise ApiError(
                INVALID_RESPONSE,
                "The provider reported this job complete but its answer matched no output the mapping describes.",
                details={"provider_job_id": job.provider_job_id},
                hint="Compare the mapping result paths against a real completed response from this provider.",
            )
        return outputs
    def cancel(self, context: AdapterContext, job: SubmittedJob) -> JobStatus:
        """Ask the provider to stop, then report what it actually says afterwards."""
        spec = self.spec_for(context)
        cancel_spec = spec.get("cancel") or {}
        path = cancel_spec.get("path")
        if not path:
            raise ApiError(
                UNSUPPORTED_OPERATION,
                "This connection mapping does not describe a cancel call.",
                details={"adapter": self.slug, "provider_job_id": job.provider_job_id},
                hint="The job will run to completion. Add a cancel path to the mapping if the provider offers one.",
            )
        url = _url_for(
            context, path, request_id=job.provider_job_id, model=getattr(job, "model_key", "")
        )
        self._request(context, cancel_spec.get("method") or "POST", url)
        # The provider own answer decides. Claiming cancellation because the call
        # returned 200 is how a job disappears from the queue while the provider
        # is still billing for it.
        try:
            live = self.poll(context, job)
        except ApiError:
            return JobStatus(
                state=STATE_CANCELLED,
                provider_status="cancel requested",
                message=(
                    "The provider accepted the cancel call but did not confirm the final state. "
                    "This job is recorded as cancelled at our request."
                ),
            )
        if live.state in (STATE_QUEUED, STATE_PROCESSING):
            live.message = (
                "The provider accepted the cancel call but still reports this job as %s."
                % (live.provider_status or live.state)
            )
        return live

    def estimate_cost(
        self, context: AdapterContext, request: GenerationRequest, profile: CapabilityProfile
    ) -> CostEstimate:
        """Unknown, and said so. A hand-written mapping carries no price table."""
        return CostEstimate(
            is_estimate=True,
            is_billable=False,
            note="Billed by your provider on your own account.",
            unknown_reason=(
                "This provider is described by a mapping you wrote, which contains no pricing information."
            ),
        )

    def parse_error(self, error: ApiError) -> ApiError:
        """Keep the provider own words and name this adapter in the details."""
        if isinstance(error.details, Mapping):
            details = dict(error.details)
        elif error.details is None:
            details = {}
        else:
            details = {"detail": error.details}
        details.setdefault("adapter", self.slug)
        error.details = details
        return error