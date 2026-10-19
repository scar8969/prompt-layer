"""Small, closed helpers for reading and writing declared JSON shapes.

The declarative adapter lets a user describe where a provider keeps its request
ids and its output URLs. That description is data, never code: this module is the
whole interpreter for it, and it can do exactly three things -- read a path,
coerce a scalar, and pick the first present key. Anything a provider needs beyond
that belongs in a real adapter written by a human.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Mapping, Optional, Sequence

_PATH_SEGMENT = re.compile(r"^([^.\[\]]+)((?:\[(\d+)\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")

#: Names a provider might use for the same concept. Used only when the user has
#: not named one explicitly, and always reported back so the guess is visible.
ID_KEYS = ("request_id", "id", "job_id", "task_id", "generation_id", "prediction_id", "uid")
STATUS_KEYS = ("status", "state", "job_status", "task_status", "prediction_status")
URL_KEYS = ("url", "uri", "href", "output_url", "file_url", "image_url", "video_url", "src")
MESSAGE_KEYS = ("message", "detail", "error", "msg", "reason", "description", "status_message")
ERROR_KEYS = ("error", "error_message", "failure", "fail_reason")
QUEUE_KEYS = ("queue_position", "queue_index", "position", "queue")
PROGRESS_KEYS = ("progress", "percent", "percentage", "completion")


def get_path(data: Any, path: Optional[str], default: Any = None) -> Any:
    """Read a dotted path with optional [n] indexes. Never raises."""
    if not path:
        return data if isinstance(data, Any) else default
    current = data
    for raw in str(path).split("."):
        if not raw:
            continue
        match = _PATH_SEGMENT.match(raw)
        if not match:
            return default
        name = match.group(1)
        if isinstance(current, Mapping):
            if name not in current:
                return default
            current = current.get(name)
        elif isinstance(current, (list, tuple)):
            # A bare name against a list means "the first element that has it".
            found = None
            for item in current:
                if isinstance(item, Mapping) and name in item:
                    found = item.get(name)
                    break
            if found is None:
                return default
            current = found
        else:
            return default
        for index in _INDEX.findall(match.group(2) or ""):
            position = int(index)
            if not isinstance(current, (list, tuple)) or position >= len(current):
                return default
            current = current[position]
    return current


def first_present(data: Any, keys: Sequence[str]) -> Any:
    if not isinstance(data, Mapping):
        return None
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def first_string(data: Any, keys: Sequence[str]) -> Optional[str]:
    value = first_present(data, keys)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        inner = first_string(value, MESSAGE_KEYS)
        if inner:
            return inner
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def percent_to_fraction(value: Any) -> Optional[float]:
    """Normalise a provider percentage into 0..1, or refuse an implausible one."""
    number = to_float(value)
    if number is None:
        return None
    if number > 1.0:
        if number <= 100.0:
            number = number / 100.0
        else:
            return None
    if number < 0.0 or number > 1.0:
        return None
    return round(number, 4)


def collect_urls(value: Any, *, depth: int = 0) -> List[str]:
    """Find output URLs in a payload whose exact shape the user did not pin down.

    Only strings that parse as http(s) URLs are returned, and only from a bounded
    depth, so this cannot wander into a large unrelated structure.
    """
    out: List[str] = []
    if depth > 6:
        return out
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://"):
            out.append(value)
        return out
    if isinstance(value, Mapping):
        for key in URL_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                if candidate not in out:
                    out.append(candidate)
        for item in value.values():
            for found in collect_urls(item, depth=depth + 1):
                if found not in out:
                    out.append(found)
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            for found in collect_urls(item, depth=depth + 1):
                if found not in out:
                    out.append(found)
    return out


def join_url(base: str, path: str) -> str:
    if not path:
        return base
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def render_template(template: str, values: Mapping[str, Any]) -> str:
    """Substitute {name} placeholders from values. Unknown names raise."""
    if not template:
        return template
    out = template
    for key, value in (values or {}).items():
        out = out.replace("{" + str(key) + "}", str(value))
    if "{" in out:
        missing = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", out)
        raise KeyError(missing[0] if missing else out)
    return out


def drop_empty(payload: Mapping[str, Any]) -> dict:
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def unique(values: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    for item in values:
        if item not in out:
            out.append(item)
    return out
