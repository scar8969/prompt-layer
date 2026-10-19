"""Small shared helpers: ids, timestamps, slugs, pagination, JSON coercion."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def new_id() -> str:
    """A random, unguessable primary key.

    Sequential integers would make every resource enumerable and would leak
    volume; the cost of a UUID here is a few bytes.
    """
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=timezone.utc)


def now_iso() -> str:
    """Fixed-width UTC timestamp.

    Fixed width matters: rows are ordered by these strings in SQL, so
    "2024-01-02T03:04:05.000006Z" must always have the same shape.
    """
    return utcnow().strftime(_ISO_FORMAT)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(_ISO_FORMAT)


def parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def iso_in(seconds: float) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))


def is_past(value: str) -> bool:
    parsed = parse_iso(value)
    return parsed is not None and parsed <= utcnow()


def slugify(value: str, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug[:64] or fallback


def unique_slug(base: str, taken: Iterable[str]) -> str:
    existing = set(taken)
    if base not in existing:
        return base
    for suffix in range(2, 500):
        candidate = "%s-%d" % (base, suffix)
        if candidate not in existing:
            return candidate
    return "%s-%s" % (base, uuid.uuid4().hex[:6])


def json_dumps(value: Any) -> str:
    """Canonical JSON for storage: stable key order, no NaN, no surprise types."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(value: Optional[str], default: Any = None) -> Any:
    """Never raises. A corrupt column should degrade to a default, not a 500."""
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def truncate(value: str, limit: int, suffix: str = "\u2026") -> str:
    if value is None:
        return value
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len(suffix))] + suffix


def merge_dicts(*sources: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Shallow merge, later wins, skipping None sources."""
    out: Dict[str, Any] = {}
    for source in sources:
        if source:
            out.update(source)
    return out


def humanize_bytes(count: int) -> str:
    step = 1024.0
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return "%.1f %s" % (value, unit) if unit != "B" else "%d B" % int(value)
        value /= step
    return "%.1f PB" % value


def group_by(items: Iterable[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        out.setdefault(str(item.get(key)), []).append(item)
    return out
