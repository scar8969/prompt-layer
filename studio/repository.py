"""The data-access layer: one repository per aggregate, every call tenant-scoped.

The rule this module exists to enforce
--------------------------------------
Every method a request handler can reach takes ``workspace_id`` as its first
argument and filters on it *inside the SQL*. Not in Python, not in a helper that a
later refactor can drop: in the WHERE clause, on every statement, including the
UPDATE that follows a read. A row belonging to another workspace therefore cannot
be returned, updated, or deleted, because no query this module issues ever looks
for it without that predicate.

A missing row and a row that belongs to somebody else are reported identically --
``ApiError(NOT_FOUND)`` with the same message -- so an id cannot be probed for
existence by watching which error comes back.

Two shapes of exception, both deliberate
----------------------------------------
* The worker's queue calls (``claim_next``, ``release_lease``, ``events``,
  ``outputs``, ``retryable``, ``stale_jobs``) are scoped by job id, because the
  worker sweeps every tenant by design. Each says so in its docstring, and the
  read-shaped ones accept a ``workspace_id`` keyword that a request-scoped caller
  must pass.
* ``ProjectRepository.attach_assets`` takes ``workspace_id`` first as well, even
  though it attaches by project id, so that attaching cannot be used to discover
  whether somebody else's project exists.

What this layer does not do
---------------------------
No HTTP, no role checks (that is :mod:`studio.security.rbac`), no credential
decryption, and no invented data. A credential arrives already sealed and leaves
only as the sealed envelope; a cost nobody reported stays unknown rather than
becoming a zero; a count of disabled models is the number of rows SQLite actually
changed.

Row shaping
-----------
SQLite stores booleans as 0/1 and JSON as TEXT. Every dict this module returns has
been through :func:`_shape_row`, so ``is_enabled`` is a real ``bool`, every
``*_at`` column is the fixed-width UTC string the rest of the platform orders by
(or ``None``), and each JSON column is published twice: parsed under its own
``*_json`` name and under the bare name (``modes`` for ``modes_json``). Callers
inside this codebase read the plain name and the raw text by the column name, and
two names for one value is cheaper than two shapes of row.

Payloads
--------
Writes accept either the structured vocabulary these methods document (``modes``,
``config``, ``metadata``, ``references``) or the column names the schema uses
(``modes_json``, ``config_json``, ``metadata_json``, ``references_json``), because
the services in this package assemble whole rows while route handlers pass request
bodies. Both arrive at the same validation -- neither shape skips a check -- and an
unknown key is refused rather than silently dropped.

The SQL never mirrors a fixed column list
-----------------------------------------
Reads build their SELECT list from the live schema (``PRAGMA table_info``), so a
migration that adds a column is reflected here without editing this module, and a
migration that renames one cannot turn every read into "no such column".
"""

from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .adapters.base import IMAGE_MODES, VIDEO_MODES
from .adapters.core import get_adapter
from .db import Database, rows_to_dicts
from .errors import (
    CAPABILITY_UNSUPPORTED,
    CONFLICT,
    CONNECTION_TEST_OUTCOMES,
    INSECURE_URL,
    MALFORMED_URL,
    NOT_FOUND,
    VALIDATION_FAILED,
    ApiError,
)
from .util import (
    clamp,
    iso,
    json_dumps,
    json_loads,
    new_id,
    now_iso,
    parse_iso,
    slugify,
    truncate,
    unique_slug,
)


# ---------------------------------------------------------------------------
# Vocabularies, copied from the migrations and from studio.adapters.base
# ---------------------------------------------------------------------------

#: ``api_connections.adapter_type`` CHECK constraint. Restated here so a create
#: fails with a field-scoped 400 instead of a raw IntegrityError.
ADAPTER_TYPES: Tuple[str, ...] = (
    "fal_queue",
    "openai_images",
    "declarative_http",
    "mock",
)

#: ``api_connections.auth_scheme`` CHECK constraint.
AUTH_SCHEMES: Tuple[str, ...] = ("bearer", "header", "query", "none")

#: ``models.modality`` allows "other"; a generation job may only be image or video.
MODEL_MODALITIES: Tuple[str, ...] = ("image", "video", "other")
JOB_MODALITIES: Tuple[str, ...] = ("image", "video")

#: The generation-mode vocabulary per modality, taken from the adapter package so
#: a mode this platform cannot request cannot be stored on a model.
MODE_VOCABULARY: Dict[str, Tuple[str, ...]] = {
    "image": tuple(IMAGE_MODES),
    "video": tuple(VIDEO_MODES),
    "other": (),
}

#: ``models.source`` CHECK constraint.
MODEL_SOURCES: Tuple[str, ...] = ("discovered", "manual")

#: Every outcome a stored connection test may carry. This is the closed list from
#: :mod:`studio.errors` plus the two conditions the connection service itself can
#: record: ``ok``, which is what a provider that actually answered produces, and
#: ``configuration_required`, which is what a deployment missing a key produces.
#: Both are real observations, so the list follows what can happen rather than only
#: the failure vocabulary -- anything else is an invented result and is refused.
TEST_OUTCOMES: Tuple[str, ...] = tuple(CONNECTION_TEST_OUTCOMES) + (
    "ok",
    "configuration_required",
)

#: ``assets.kind`` and ``assets.source`` CHECK constraints.
ASSET_KINDS: Tuple[str, ...] = ("image", "video", "audio", "other")
ASSET_SOURCES: Tuple[str, ...] = ("upload", "generated", "derived")

#: ``projects.status`` CHECK constraint.
PROJECT_STATUSES: Tuple[str, ...] = ("active", "archived")

#: ``presets.kind`` CHECK constraint.
PRESET_KINDS: Tuple[str, ...] = ("image", "video")

#: ``usage_records.billing_party`` CHECK constraint. Who pays is recorded per row
#: so a report can never mix the two parties.
BILLING_PARTIES: Tuple[str, ...] = ("user_provider", "platform")

#: Every state ``generation_jobs.status`` may hold, and the only moves allowed
#: between them. A state is not in its own set by default: repeating "completed"
#: on a finished job is refused, because the second write would append an event
#: claiming something changed when nothing did. The two in-flight states are the
#: deliberate exception -- a polling worker re-asserts "processing" on every pass
#: to record what the provider said, and that observation is a real event, not a
#: fake transition.
LEGAL_TRANSITIONS: Dict[str, frozenset] = {
    "draft": frozenset({"queued", "cancelled", "failed"}),
    "queued": frozenset({"validating", "submitting", "cancelled", "failed", "timed_out"}),
    "validating": frozenset({"queued", "submitting", "failed", "cancelled", "timed_out"}),
    "submitting": frozenset({"queued", "processing", "failed", "cancelled", "timed_out"}),
    "processing": frozenset(
        {"processing", "downloading", "completed", "failed", "cancelled", "timed_out"}
    ),
    "downloading": frozenset(
        {"downloading", "completed", "failed", "cancelled", "timed_out"}
    ),
    "completed": frozenset(),
    "failed": frozenset({"queued"}),
    "cancelled": frozenset(),
    "timed_out": frozenset({"queued"}),
}

#: Every job state, in the order the migration lists them.
JOB_STATES: Tuple[str, ...] = tuple(LEGAL_TRANSITIONS)

#: States a job never leaves on its own. ``failed`` and ``timed_out`` can still
#: be requeued for a retry, but they are terminal for the purpose of cancelling:
#: asking to cancel something that already stopped is a conflict, not a no-op.
TERMINAL_STATES: frozenset = frozenset({"completed", "failed", "cancelled", "timed_out"})

#: States that mean "work is somewhere in the pipeline".
ACTIVE_STATES: frozenset = frozenset(
    {"draft", "queued", "validating", "submitting", "processing", "downloading"}
)

#: Aliases accepted by :meth:`JobRepository.list` for callers that want a bucket
#: rather than one exact state.
_STATE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "active": tuple(sorted(ACTIVE_STATES)),
    "terminal": tuple(sorted(TERMINAL_STATES)),
}


# ---------------------------------------------------------------------------
# Row shaping
# ---------------------------------------------------------------------------

#: Columns that hold JSON text, per table. The parsed value replaces the text
#: under the same key, so a caller never has to remember which columns are JSON.
_JSON_COLUMNS: Dict[str, frozenset] = {
    "api_connections": frozenset(
        {"extra_headers_json", "config_json", "allowed_hosts_json", "last_test_detail_json"}
    ),
    "connection_tests": frozenset({"detail_json"}),
    "models": frozenset(
        {
            "modes_json",
            "capabilities_json",
            "parameter_schema_json",
            "defaults_json",
            "limits_json",
        }
    ),
    "projects": frozenset({"tags_json"}),
    "assets": frozenset({"metadata_json"}),
    "generation_jobs": frozenset(
        {
            "parameters_json",
            "request_json",
            "references_json",
            "provider_error_json",
            "cost_estimate_json",
        }
    ),
    "generation_outputs": frozenset(),
    "job_events": frozenset({"detail_json"}),
    "presets": frozenset(
        {
            "defaults_json",
            "supported_modes_json",
            "required_references_json",
            "camera_settings_json",
            "tags_json",
        }
    ),
    "saved_prompts": frozenset({"variables_json", "tags_json"}),
    "characters": frozenset({"attributes_json", "preferred_models_json", "tags_json"}),
    "character_references": frozenset(),
    "usage_records": frozenset({"units_json", "estimated_cost_json", "actual_cost_json"}),
    "audit_log": frozenset({"detail_json"}),
}

#: JSON columns whose natural empty value is a list rather than an object.
_LIST_JSON_COLUMNS = frozenset(
    {
        "references_json",
        "allowed_hosts_json",
        "modes_json",
        "tags_json",
        "supported_modes_json",
        "required_references_json",
        "variables_json",
        "preferred_models_json",
    }
)

#: JSON columns where NULL is meaningful and must not be flattened to ``{}``.
#: An absent ``actual_cost_json`` means "the provider reported no price", which is
#: not the same statement as "the price was zero".
_NULLABLE_JSON_COLUMNS = frozenset({"actual_cost_json", "provider_error_json"})

#: 0/1 columns that should read as booleans.
_BOOL_COLUMNS: Dict[str, frozenset] = {
    "api_connections": frozenset({"is_enabled"}),
    "models": frozenset({"is_enabled", "is_favorite", "is_default"}),
    "assets": frozenset({"is_favorite"}),
    "presets": frozenset({"is_builtin"}),
}


def _iso(value: Any) -> Optional[str]:
    """Normalise a timestamp to the fixed-width UTC text the schema orders by.

    ``None`` and the empty string both mean "never happened" and become ``None``;
    a ``datetime`` is formatted with the platform's one format; anything else is
    passed through as stripped text, so a value already written by
    :func:`studio.util.now_iso` is not re-parsed and cannot drift.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _int_flag(value: Any) -> bool:
    """Read a SQLite 0/1 column as a real ``bool``.

    Handles the three shapes actually found in this database: an int, a bool from
    a caller that has not round-tripped through SQL yet, and the text "1"/"true"
    that appears when a fixture wrote the column by hand. NULL reads as ``False``
    because every flag column in the schema is NOT NULL DEFAULT 0; a NULL can
    therefore only mean a row that was never written properly, and treating that
    as "on" would be the dangerous guess.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(int(value))


def _json(value: Any, default: Any = None) -> Any:
    """Parse a JSON column, degrading to ``default`` instead of raising.

    A corrupt column should cost the caller one empty field, not a 500. The
    default is what tells the difference between "absent" (``None``) and "empty"
    (``{}`` or ``[]``), which matters for the cost columns.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    return json_loads(value, default)


def _json_default(column: str) -> Any:
    """The empty value for a JSON column, decided by the column's role."""
    if column in _NULLABLE_JSON_COLUMNS:
        return None
    if column in _LIST_JSON_COLUMNS:
        return []
    return {}


def _shape_row(table: str, row: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return one row with booleans, JSON and timestamps normalised.

    A JSON column is published twice: once under its own ``*_json`` name, parsed,
    and once under the bare name (``modes`` for ``modes_json``), because the rest of
    the platform reads a row's parsed value by the plain name and its raw text by
    the column name. Both refer to the same value, so a caller cannot get one
    without the other and discover a half-populated row.
    """
    if row is None:
        return None
    shaped: Dict[str, Any] = dict(row)
    for column in _JSON_COLUMNS.get(table, frozenset()):
        if column in shaped:
            parsed = _json(shaped[column], _json_default(column))
            shaped[column] = parsed
            shaped[column[: -len("_json")]] = parsed
    for column in _BOOL_COLUMNS.get(table, frozenset()):
        if column in shaped:
            shaped[column] = _int_flag(shaped[column])
    for column, value in shaped.items():
        if column.endswith("_at") and isinstance(value, (datetime, str)):
            shaped[column] = _iso(value)
    return shaped


# ---------------------------------------------------------------------------
# Small validation helpers
# ---------------------------------------------------------------------------


def _mapping(payload: Any, what: str) -> Dict[str, Any]:
    """Coerce a payload to a dict, refusing anything else by name."""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ApiError(VALIDATION_FAILED, "The %s payload must be an object." % what)
    return dict(payload)


def _reject_unknown(values: Mapping[str, Any], allowed: Iterable[str], what: str) -> None:
    """Refuse keys this layer would otherwise drop silently.

    :meth:`Database.insert` filters unknown columns out, which is right for a
    generic helper and wrong for a repository: a typo in a field name would be
    stored as nothing at all and the caller would be told it succeeded.
    """
    known = set(allowed)
    unknown = sorted(key for key in values if key not in known)
    if unknown:
        raise ApiError(
            VALIDATION_FAILED,
            "The %s payload contains fields this platform does not store: %s."
            % (what, ", ".join(unknown)),
            details={"unknown_fields": unknown, "allowed": sorted(known)},
        )


#: Column-shaped payload keys mapped onto the structured names this layer uses.
#: A caller may hand this module either the vocabulary it documents or a row it
#: assembled from the schema; both describe the same record, and refusing the
#: second would only force every caller to duplicate the translation.
_CONNECTION_ALIASES: Dict[str, str] = {
    "extra_headers_json": "extra_headers",
    "config_json": "config",
    "allowed_hosts_json": "allowed_hosts",
}
_MODEL_ALIASES: Dict[str, str] = {
    "modes_json": "modes",
    "capabilities_json": "capabilities",
    "parameter_schema_json": "parameter_schema",
    "defaults_json": "defaults",
    "limits_json": "limits",
}
_ASSET_ALIASES: Dict[str, str] = {"metadata_json": "metadata"}
_JOB_ALIASES: Dict[str, str] = {
    "parameters_json": "parameters",
    "request_json": "request",
    "references_json": "references",
    "cost_estimate_json": "cost_estimate",
}
_USAGE_ALIASES: Dict[str, str] = {
    "units_json": "units",
    "estimated_cost_json": "estimated_cost",
    "actual_cost_json": "actual_cost",
}
_TEST_ALIASES: Dict[str, str] = {"detail_json": "detail"}
_LIBRARY_ALIASES: Dict[str, str] = {
    "defaults_json": "defaults",
    "supported_modes_json": "supported_modes",
    "required_references_json": "required_references",
    "camera_settings_json": "camera_settings",
    "tags_json": "tags",
    "variables_json": "variables",
    "attributes_json": "attributes",
    "preferred_models_json": "preferred_models",
}


def _absorb_row(
    values: Mapping[str, Any],
    aliases: Mapping[str, str],
    what: str,
    *,
    scope: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str], Optional[str]]:
    """Normalise a payload that may arrive in either shape.

    Authorises nothing by itself. It refuses a payload whose ``workspace_id``
    disagrees with the scope the method was called with: the argument is
    authoritative, and a mismatch means the caller is holding a row from somewhere
    else.

    Returns the structured payload, the row id the caller wants to use (if any),
    and the owner id the payload names (if any). A caller-supplied id matters: a
    credential or an object key is often bound to it before the row exists, so
    ignoring it would produce a row the rest of the system cannot reach.
    """
    out = dict(values)
    for column, name in aliases.items():
        if column not in out:
            continue
        raw = out.pop(column)
        if out.get(name) is None:
            out[name] = _json(raw, _json_default(column)) if isinstance(raw, str) else raw
    row_id = _text(out.pop("id", None))
    owner = _text(out.pop("owner_user_id", None))
    created_by = _text(out.pop("created_by_user_id", None))
    echoed_scope = _text(out.pop("workspace_id", None))
    if echoed_scope is not None and scope is not None and echoed_scope != scope:
        raise ApiError(
            VALIDATION_FAILED,
            "The %s payload names a different workspace than the request." % what,
            field="workspace_id",
        )
    out.pop("created_at", None)
    out.pop("updated_at", None)
    return out, row_id, owner or created_by


def _require_workspace(workspace_id: Any) -> str:
    """A scope is not optional. An empty one is refused before any SQL runs."""
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ApiError(
            VALIDATION_FAILED,
            "A workspace id is required for this operation.",
            field="workspace_id",
        )
    return workspace_id.strip()


def _text(value: Any) -> Optional[str]:
    """Stripped text, or ``None`` for absent and blank values."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _require_text(value: Any, field: str, *, limit: int = 0) -> str:
    """A field that must be present and non-blank."""
    text = _text(value)
    if not text:
        raise ApiError(
            VALIDATION_FAILED,
            "%s is required." % field.replace("_", " ").capitalize(),
            field=field,
        )
    if limit and len(text) > limit:
        raise ApiError(
            VALIDATION_FAILED,
            "%s must be %d characters or fewer."
            % (field.replace("_", " ").capitalize(), limit),
            field=field,
            details={"limit": limit, "length": len(text)},
        )
    return text


def _choice(
    value: Any, allowed: Sequence[str], field: str, *, default: Optional[str] = None
) -> str:
    """A value that must come from a closed vocabulary."""
    text = _text(value)
    if text is None:
        if default is not None:
            return default
        raise ApiError(
            VALIDATION_FAILED,
            "%s is required." % field.replace("_", " ").capitalize(),
            field=field,
        )
    if text not in allowed:
        raise ApiError(
            VALIDATION_FAILED,
            "%s must be one of: %s."
            % (field.replace("_", " ").capitalize(), ", ".join(allowed)),
            field=field,
            details={"allowed": list(allowed)},
        )
    return text


def _flag(value: Any, default: bool = False) -> bool:
    """An optional boolean that also accepts 0/1, "true"/"false" and "on"/"off"."""
    if value is None:
        return default
    return _int_flag(value)


def _optional_int(value: Any) -> Optional[int]:
    """An optional integer, refusing text that is not one."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(VALIDATION_FAILED, "Expected a whole number, got %r." % (value,)) from None


def _optional_float(value: Any) -> Optional[float]:
    """An optional number, refusing text that is not one."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(VALIDATION_FAILED, "Expected a number, got %r." % (value,)) from None


def _mode_list(value: Any) -> List[str]:
    """Normalise a modes payload: a list, a tuple, or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        items: List[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ApiError(
            VALIDATION_FAILED,
            "modes must be a list of mode names.",
            field="modes",
        )
    modes: List[str] = []
    for item in items:
        text = _text(item)
        if text and text not in modes:
            modes.append(text)
    return modes


def _check_modes(modality: str, modes: Sequence[str]) -> None:
    """Refuse a mode the modality cannot perform, naming the mode.

    This is the check that stops an image model from claiming to extend a video.
    The refusal is ``CAPABILITY_UNSUPPORTED`` rather than a validation error
    because the caller asked for a capability, and the honest answer is that the
    platform cannot request it -- not that the request was malformed.
    """
    vocabulary = MODE_VOCABULARY.get(modality, ())
    for mode in modes:
        if mode not in vocabulary:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "Mode '%s' does not belong to modality '%s'." % (mode, modality),
                field="modes",
                details={
                    "mode": mode,
                    "modality": modality,
                    "supported_modes": list(vocabulary),
                },
            )


def _normalise_url(value: Any, field: str = "base_url") -> str:
    """Require an absolute http(s) URL with a host.

    Whether plain http is acceptable to this deployment is egress policy's
    decision (see :mod:`studio.net`), not this layer's. What is refused here is a
    value that could not become a request at all: no scheme, no host, a file path
    standing in for a URL, or a scheme that is not http(s).
    """
    text = _require_text(value, field)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise ApiError(
            INSECURE_URL if parts.scheme else MALFORMED_URL,
            "The base URL must start with http:// or https://.",
            field=field,
        )
    if not parts.netloc:
        raise ApiError(
            MALFORMED_URL,
            "The base URL must name a host, for example https://api.example.com/v1.",
            field=field,
        )
    return text


def _like(term: str) -> str:
    """A LIKE pattern with the caller's wildcards escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def _placeholders(count: int) -> str:
    """``?, ?, ?`` for an IN clause of the given size."""
    return ", ".join("?" for _ in range(count))


def _not_found(what: str) -> ApiError:
    """The one not-found error, identical for absent and for foreign ids."""
    return ApiError(NOT_FOUND, "That %s could not be found." % what)


def _conflict_for(error: sqlite3.IntegrityError, what: str) -> ApiError:
    """Turn the two interesting constraint failures into the right code.

    A UNIQUE violation means the name or key is taken: a conflict the caller can
    fix by choosing another one. A CHECK violation means a value got past this
    layer's own validation, which is a validation failure. Anything else (a
    foreign key to a vanished row, say) is reported as not-found, because the
    thing being referenced does not exist in this workspace.
    """
    message = str(error).upper()
    if "UNIQUE" in message:
        return ApiError(CONFLICT, "That %s already exists in this workspace." % what)
    if "CHECK" in message:
        return ApiError(VALIDATION_FAILED, "A value was refused by the database (%s)." % what)
    return ApiError(NOT_FOUND, "A referenced %s could not be found." % what)


class _Repository:
    """Shared plumbing: one Database handle, the shape helpers, common refusals."""

    #: The table this repository mostly reads, used by the shape helpers.
    TABLE = ""

    def __init__(self, db: Database) -> None:
        """Bind to one database handle.

        Authorises nothing: a repository constructor grants no access.

        Refuses a handle it was not given (there is no default) and a value that is
        not a :class:`studio.db.Database`, which is what keeps the database a
        dependency rather than a global and keeps a typo from surfacing later as an
        AttributeError somewhere unrelated.
        """
        if not isinstance(db, Database):
            raise TypeError(
                "A repository needs a studio.db.Database, got %r." % (type(db).__name__,)
            )
        self._db = db
        self._projections: Dict[Tuple[str, str], str] = {}

    # -- projection ---------------------------------------------------------
    def _projection(self, table: str, alias: str) -> str:
        """An aliased column list built from the table as it actually exists.

        Authorises nothing. Refuses to reference a column that is not in the
        schema: the list comes from ``PRAGMA table_info`` rather than from a
        constant, so a migration that adds or renames a column cannot turn every
        read into "no such column" -- and, just as importantly, a column added
        later is returned instead of being silently invisible.
        """
        key = (table, alias)
        cached = self._projections.get(key)
        if cached is None:
            columns = self._db.table_columns(table)
            if not columns:
                raise ApiError(
                    INTERNAL,
                    "The schema has no table called '%s'." % table,
                    details={"table": table},
                )
            cached = ", ".join('%s."%s"' % (alias, name) for name in columns)
            self._projections[key] = cached
        return cached

    # -- reads --------------------------------------------------------------
    def _all(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        table: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run a read and shape every row.

        Authorises nothing on its own -- the caller's SQL is the authority, and
        every caller builds it from ``workspace_id`` plus fixed text. It refuses to
        guess a table: the shape applied is the one named, so a JSON column cannot
        be parsed with another table's rules.
        """
        name = table or self.TABLE
        return [
            _shape_row(name, row)  # type: ignore[misc]
            for row in rows_to_dicts(self._db.query(sql, params))
        ]

    def _one(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        table: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run a read expected to match at most one row, shaped like ``_all``.

        Authorises nothing beyond what the caller's SQL allows. Refuses to invent
        a row: ``None`` is returned as ``None``, and turning that into an error is
        the caller's decision (usually ``_scoped_row`` doing it).
        """
        name = table or self.TABLE
        row = self._db.query_dict(sql, params)
        return _shape_row(name, row) if row is not None else None

    def _count(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a counting read.

        Authorises nothing on its own. Refuses to return ``None``: an absent count
        is a zero, because every caller here is counting rows it has already
        scoped, not looking for the existence of something.
        """
        return int(self._db.scalar(sql, params, default=0) or 0)

    def _scoped_row(
        self,
        sql: str,
        params: Sequence[Any],
        what: str,
        *,
        table: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A scoped read that must match exactly one row, or ``NOT_FOUND``.

        Authorises: exactly what the caller's SQL allows -- which is always a
        ``workspace_id`` predicate. Refuses an absent or foreign row with the same
        ``NOT_FOUND`` error, so the caller learns nothing about whether the id
        exists elsewhere.
        """
        row = self._one(sql, params, table=table)
        if row is None:
            raise _not_found(what)
        return row


# ---------------------------------------------------------------------------
# Connections and their models
# ---------------------------------------------------------------------------

#: Every column of ``api_connections`` a caller may write through this layer.
#: The four credential columns are absent on purpose: they are written only by
#: :meth:`ConnectionRepository.create` (with a value the caller has already
#: sealed) or by :meth:`ConnectionRepository.rotate_credential`, so a stale form
#: submission cannot re-seal an old key over a new one.
CONNECTION_FIELDS: Tuple[str, ...] = (
    "name",
    "provider_slug",
    "adapter_type",
    "base_url",
    "auth_scheme",
    "auth_header_name",
    "extra_headers",
    "config",
    "allowed_hosts",
)

#: The sealed envelope. Named without the ``credential_`` prefix in the payload
#: because the caller holds a sealed value, not a credential.
SEALED_FIELDS: Dict[str, str] = {
    "credential_ciphertext": "ciphertext",
    "credential_key_id": "key_id",
    "credential_last4": "last4",
    "credential_fingerprint": "fingerprint",
}

#: Payload keys accepted by :meth:`ConnectionRepository.create`.
_CONNECTION_CREATE_FIELDS: Tuple[str, ...] = (
    CONNECTION_FIELDS
    + tuple(SEALED_FIELDS)
    + ("credential", "is_enabled")
)

#: Header names that must never sit in the ``extra_headers_json`` column. The
#: migration says secret-bearing names are rejected on write; this is that
#: rejection. Such a value belongs in the sealed credential, where it is
#: encrypted and never rendered back to the browser.
_SECRET_HEADER_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-api-token",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
        "cookie",
        "set-cookie",
    }
)


class ConnectionRepository(_Repository):
    """Provider connections: the credentials a workspace has entrusted us with.

    Every method here is scoped to one workspace, and ``credential_of`` is the
    single place a sealed envelope can be read back -- never a decrypted key,
    because this layer has no key material and no way to obtain any.
    """

    TABLE = "api_connections"

    #: The projection every read uses. Listing the columns rather than
    #: ``SELECT *`` means a future migration that adds a column cannot leak it
    #: into an API response by accident.
    _COLUMNS = (
        "c.id, c.workspace_id, c.created_by_user_id, c.name, c.provider_slug,"
        " c.adapter_type, c.base_url, c.auth_scheme, c.auth_header_name,"
        " c.credential_ciphertext, c.credential_key_id, c.credential_last4,"
        " c.credential_fingerprint, c.extra_headers_json, c.config_json,"
        " c.allowed_hosts_json, c.is_enabled, c.last_test_status, c.last_test_at,"
        " c.last_test_detail_json, c.last_used_at, c.created_at, c.updated_at,"
        " c.deleted_at"
    )

    # -- reads --------------------------------------------------------------
    def list(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Every live connection in one workspace, newest name last.

        Authorises: reads connection metadata for the given workspace only,
        including the *sealed* envelope -- ciphertext, key id, last four and
        fingerprint -- because the caller above this layer has to be able to say
        "a key is present, ending 1234" and cannot invent that from nothing. The
        envelope is sealed: it is not plaintext, and it is useless without the key
        that lives outside the database. :meth:`credential_of` remains the only
        function that hands it over for *opening*, and :mod:`studio.routes` strips
        these columns before anything reaches a response.

        Refuses: to return a connection from another workspace, or a soft-deleted
        one. Refuses an empty workspace id.
        """
        scope = _require_workspace(workspace_id)
        rows = self._db.query(
            "SELECT " + self._COLUMNS + ","
            " (SELECT COUNT(*) FROM models m WHERE m.connection_id = c.id)"
            " AS model_count"
            " FROM api_connections c"
            " WHERE c.workspace_id = ? AND c.deleted_at IS NULL"
            " ORDER BY c.name COLLATE NOCASE ASC",
            (scope,),
        )
        out: List[Dict[str, Any]] = []
        for row in rows_to_dicts(rows):
            shaped = _shape_row(self.TABLE, row) or {}
            shaped["has_credential"] = bool(row.get("credential_ciphertext"))
            out.append(shaped)
        return out

    def get(self, workspace_id: str, connection_id: str) -> Dict[str, Any]:
        """One live connection, by id, inside one workspace.

        Authorises: reading the metadata of a connection the workspace owns.

        Refuses: a connection that does not exist, belongs to another workspace,
        or has been soft-deleted -- all three with the same ``NOT_FOUND``, so an
        id cannot be tested for existence. The sealed envelope is included for the
        same reason it is included by :meth:`list`: a caller that cannot see
        whether a key exists will report that none does, and that would be a lie
        shown to the person who just pasted one.
        """
        scope = _require_workspace(workspace_id)
        row = self._db.query_dict(
            "SELECT " + self._COLUMNS + ","
            " (SELECT COUNT(*) FROM models m WHERE m.connection_id = c.id)"
            " AS model_count"
            " FROM api_connections c"
            " WHERE c.id = ? AND c.workspace_id = ? AND c.deleted_at IS NULL",
            (connection_id, scope),
        )
        if row is None:
            raise _not_found("connection")
        shaped = _shape_row(self.TABLE, row) or {}
        shaped["has_credential"] = bool(row.get("credential_ciphertext"))
        return shaped

    def credential_of(self, workspace_id: str, connection_id: str) -> Dict[str, Any]:
        """The sealed credential envelope, for the one caller allowed to open it.

        Authorises: returning ``ciphertext``, ``key_id``, ``last4`` and
        ``fingerprint`` for a live connection in this workspace. This exists so
        that exactly one function in the codebase reads key material, and so a
        grep for the column name finds it.

        Refuses: a connection in another workspace, a deleted connection, and a
        connection whose credential was cleared by :meth:`soft_delete` -- in every
        one of those cases ``NOT_FOUND``, identically. Returns ``NOT_FOUND`` when
        there is no credential at all rather than a dict of ``None`` values, so
        the caller must handle "no credential" explicitly.
        """
        scope = _require_workspace(workspace_id)
        row = self._db.query_dict(
            "SELECT credential_ciphertext, credential_key_id, credential_last4,"
            " credential_fingerprint FROM api_connections"
            " WHERE id = ? AND workspace_id = ? AND deleted_at IS NULL",
            (connection_id, scope),
        )
        if row is None:
            raise _not_found("connection")
        if not row["credential_ciphertext"]:
            raise ApiError(
                NOT_FOUND,
                "That connection has no stored credential.",
                hint="Rotate the credential to supply one.",
            )
        return {
            "ciphertext": row["credential_ciphertext"],
            "key_id": row["credential_key_id"],
            "last4": row["credential_last4"],
            "fingerprint": row["credential_fingerprint"],
        }

    # -- writes -------------------------------------------------------------
    def create(self, workspace_id: str, user_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Register a provider connection from an already-sealed credential.

        Authorises: inserting a connection into the given workspace, attributed
        to ``user_id``. The payload carries the sealed envelope
        (``credential_ciphertext``, ``credential_key_id``,
        ``credential_last4``, ``credential_fingerprint``); this layer stores it
        verbatim and never sees plaintext.

        Refuses: a missing workspace or user id, an adapter type or auth scheme
        outside the schema's vocabulary, a base URL that is not an absolute
        http(s) URL, a header name that looks like a secret bearer token, an
        unknown field, and a name already used by a live or deleted connection in
        that workspace (``CONFLICT``).

        It also refuses to create a connection with no sealed credential, with one
        stated exception: ``auth_scheme="none"``, which is a deployment saying "this
        endpoint genuinely needs no key" (a self-hosted server behind a private
        network, or the development mock). For every other scheme a connection with
        no key is a button that fails later, so it is refused now rather than at
        the user's first generation.
        """
        scope = _require_workspace(workspace_id)
        owner = _require_text(user_id, "user_id")
        values = _mapping(payload, "connection")
        sealed = values.get("credential")
        if sealed is not None and not isinstance(sealed, Mapping):
            raise ApiError(
                VALIDATION_FAILED,
                "The credential must be the sealed envelope object.",
                field="credential",
            )
        values, row_id, echoed_owner = _absorb_row(
            values, _CONNECTION_ALIASES, "connection", scope=scope
        )
        if echoed_owner is not None and echoed_owner != owner:
            raise ApiError(
                VALIDATION_FAILED,
                "The connection payload names a different creator than the request.",
                field="created_by_user_id",
            )
        _reject_unknown(values, _CONNECTION_CREATE_FIELDS, "connection")

        name = _require_text(values.get("name"), "name", limit=120)
        adapter_type = _choice(values.get("adapter_type"), ADAPTER_TYPES, "adapter_type")
        base_url = _normalise_url(values.get("base_url"))
        auth_scheme = _choice(values.get("auth_scheme"), AUTH_SCHEMES, "auth_scheme", default="bearer")
        auth_header_name = _text(values.get("auth_header_name")) or "Authorization"
        provider_slug = _text(values.get("provider_slug")) or "custom"
        extra_headers = _header_map(values.get("extra_headers"))
        config = _json_payload(values.get("config"), "config")
        allowed_hosts = _host_list(values.get("allowed_hosts"))

        if sealed is None:
            sealed = values
        ciphertext = _text(_get(sealed, "ciphertext", "credential_ciphertext"))
        if not ciphertext and auth_scheme != "none":
            raise ApiError(
                VALIDATION_FAILED,
                "A sealed credential is required to create a connection.",
                field="credential",
                hint="Seal the key with studio.crypto before calling this layer, "
                "or declare auth_scheme 'none' if the endpoint genuinely needs no key.",
            )
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "name": name,
            "provider_slug": provider_slug,
            "adapter_type": adapter_type,
            "base_url": base_url,
            "auth_scheme": auth_scheme,
            "auth_header_name": auth_header_name,
            "credential_ciphertext": ciphertext,
            "credential_key_id": _text(_get(sealed, "key_id", "credential_key_id")),
            "credential_last4": _text(_get(sealed, "last4", "credential_last4")),
            "credential_fingerprint": _text(_get(sealed, "fingerprint", "credential_fingerprint")),
            "extra_headers_json": json_dumps(extra_headers),
            "config_json": json_dumps(config),
            "allowed_hosts_json": json_dumps(allowed_hosts),
            "is_enabled": 1 if _flag(values.get("is_enabled"), True) else 0,
        }
        try:
            with self._db.write_tx() as tx:
                inserted = tx.insert(self.TABLE, record)
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "connection name") from error
        return self.get(scope, inserted["id"])

    def update(
        self, workspace_id: str, connection_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a connection's non-secret settings.

        Authorises: updating name, provider slug, adapter type, base URL, auth
        scheme, auth header name, extra headers, config and allowed hosts -- but
        only for a live connection in this workspace.

        Refuses: a credential column (``CONFLICT``/validation error pointing at
        :meth:`rotate_credential`), an attempt to write ``workspace_id`` or
        ``deleted_at``, an unknown field, a value outside a vocabulary, a secret
        header name, and a connection that is missing or in another workspace
        (``NOT_FOUND``). Updating the model's connection is not possible here:
        ``connection_id`` is not a writable field.
        """
        scope = _require_workspace(workspace_id)
        values, _row_id, _owner = _absorb_row(
            _mapping(fields, "connection"), _CONNECTION_ALIASES, "connection", scope=scope
        )
        if any(key in SEALED_FIELDS for key in values) or "credential" in values:
            raise ApiError(
                VALIDATION_FAILED,
                "A credential is changed through rotate_credential, not through an update.",
                field="credential",
            )
        _reject_unknown(values, CONNECTION_FIELDS + ("is_enabled",), "connection")
        self.get(scope, connection_id)  # scope check first: 404 before any validation

        updates: Dict[str, Any] = {}
        if "name" in values:
            updates["name"] = _require_text(values.get("name"), "name", limit=120)
        if "provider_slug" in values:
            updates["provider_slug"] = _text(values.get("provider_slug")) or "custom"
        if "adapter_type" in values:
            updates["adapter_type"] = _choice(values.get("adapter_type"), ADAPTER_TYPES, "adapter_type")
        if "base_url" in values:
            updates["base_url"] = _normalise_url(values.get("base_url"))
        if "auth_scheme" in values:
            updates["auth_scheme"] = _choice(values.get("auth_scheme"), AUTH_SCHEMES, "auth_scheme")
        if "auth_header_name" in values:
            updates["auth_header_name"] = _text(values.get("auth_header_name")) or "Authorization"
        if "extra_headers" in values:
            updates["extra_headers_json"] = json_dumps(_header_map(values.get("extra_headers")))
        if "config" in values:
            updates["config_json"] = json_dumps(_json_payload(values.get("config"), "config"))
        if "allowed_hosts" in values:
            updates["allowed_hosts_json"] = json_dumps(_host_list(values.get("allowed_hosts")))
        if "is_enabled" in values:
            updates["is_enabled"] = 1 if _flag(values.get("is_enabled"), True) else 0
        if not updates:
            return self.get(scope, connection_id)

        try:
            with self._db.write_tx() as tx:
                self._db.update_where(
                    self.TABLE,
                    updates,
                    "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                    (connection_id, scope),
                )
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "connection name") from error
        return self.get(scope, connection_id)

    def set_enabled(self, workspace_id: str, connection_id: str, enabled: bool) -> Dict[str, Any]:
        """Turn a connection on or off without touching its credential.

        Authorises: flipping ``is_enabled`` on a live connection in this
        workspace. Disabling stops new submissions through the adapter; it does
        not delete the key and does not touch jobs already running.

        Refuses: a connection that is missing, deleted, or in another workspace
        (``NOT_FOUND``). Refuses nothing else -- the flag is the caller's decision.
        """
        return self.update(workspace_id, connection_id, {"is_enabled": bool(enabled)})

    def rotate_credential(
        self, workspace_id: str, connection_id: str, sealed: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Replace a connection's sealed credential.

        Authorises: overwriting the four credential columns of a live connection
        in this workspace with a freshly sealed envelope. The old envelope is
        gone after the commit; nothing keeps it, by design.

        Refuses: an empty or malformed envelope (a ciphertext is mandatory), an
        envelope with no ``key_id`` -- which would make the ciphertext
        undecryptable after a key rotation -- and a connection that is missing,
        deleted, or in another workspace (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        envelope = _mapping(sealed, "credential")
        ciphertext = _text(_get(envelope, "ciphertext", "credential_ciphertext"))
        if not ciphertext:
            raise ApiError(
                VALIDATION_FAILED,
                "A sealed credential ciphertext is required.",
                field="credential",
            )
        key_id = _text(_get(envelope, "key_id", "credential_key_id"))
        if not key_id:
            raise ApiError(
                VALIDATION_FAILED,
                "A sealed credential must name the key that sealed it.",
                field="key_id",
            )
        self.get(scope, connection_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {
                    "credential_ciphertext": ciphertext,
                    "credential_key_id": key_id,
                    "credential_last4": _text(_get(envelope, "last4", "credential_last4")),
                    "credential_fingerprint": _text(
                        _get(envelope, "fingerprint", "credential_fingerprint")
                    ),
                },
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (connection_id, scope),
            )
        return self.get(scope, connection_id)

    def record_test(
        self, workspace_id: str, connection_id: str, result: Mapping[str, Any] | Any
    ) -> Dict[str, Any]:
        """Append a real connection test and stamp the connection with it.

        Authorises: writing one ``connection_tests`` row and copying its outcome,
        timestamp and detail onto ``last_test_status``, ``last_test_at`` and
        ``last_test_detail_json``, for a live connection in this workspace.
        Accepts the dict from :meth:`ConnectionTestResult.to_public` or the
        result object itself.

        Refuses: an outcome outside :data:`TEST_OUTCOMES` -- the closed list the
        wizard and the service can actually produce -- because a status the UI has
        no copy for cannot be stored honestly. It also refuses a connection that is
        missing, deleted, or in another workspace (``NOT_FOUND``). It does not
        invent a latency or a status code the caller did not observe: absent means
        NULL, not zero.
        """
        scope = _require_workspace(workspace_id)
        payload = result.to_public() if hasattr(result, "to_public") else _mapping(result, "test")
        payload, _row, _owner = _absorb_row(payload, _TEST_ALIASES, "connection test")
        outcome = _require_text(payload.get("outcome"), "outcome")
        if outcome not in TEST_OUTCOMES:
            raise ApiError(
                VALIDATION_FAILED,
                "That is not a connection-test outcome this platform reports.",
                field="outcome",
                details={"allowed": list(TEST_OUTCOMES)},
            )
        self.get(scope, connection_id)
        observed_at = _iso(payload.get("created_at")) or now_iso()
        http_status = _optional_int(_get(payload, "http_status", "status"))
        latency_ms = _optional_int(_get(payload, "latency_ms", "latency"))
        detail = _json_payload(payload.get("detail"), "detail")
        if payload.get("message"):
            detail = dict(detail)
            detail.setdefault("message", truncate(str(payload.get("message")), 500))
        if payload.get("models_seen"):
            detail = dict(detail)
            detail.setdefault("models_seen", list(payload.get("models_seen") or []))

        with self._db.write_tx() as tx:
            tx.insert(
                "connection_tests",
                {
                    "id": new_id(),
                    "connection_id": connection_id,
                    "workspace_id": scope,
                    "outcome": outcome,
                    "http_status": http_status,
                    "latency_ms": latency_ms,
                    "detail_json": json_dumps(detail),
                    "created_at": observed_at,
                },
            )
            self._db.update_where(
                self.TABLE,
                {
                    "last_test_status": outcome,
                    "last_test_at": observed_at,
                    "last_test_detail_json": json_dumps(detail),
                },
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (connection_id, scope),
            )
        return self.get(scope, connection_id)

    def touch_used(self, workspace_id: str, connection_id: str) -> None:
        """Record that a request actually went out through this connection.

        Authorises: setting ``last_used_at`` on a live connection in this
        workspace. Called after a provider call, so "used" means observed rather
        than scheduled.

        Refuses: a connection that is missing, deleted, or in another workspace
        (``NOT_FOUND``). Writes nothing and raises nothing when the row is gone
        between the job starting and this call -- that race is real, and turning
        it into an error would fail a job for a bookkeeping write.

        (Implementation note: the update is scoped, so a foreign id updates zero
        rows; the caller then learns nothing about the row's existence. For the
        request-scoped path ``get`` is called first and ``NOT_FOUND`` is raised;
        for the worker path the call is best-effort by construction.)
        """
        scope = _require_workspace(workspace_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"last_used_at": now_iso()},
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (connection_id, scope),
            )

    def soft_delete(self, workspace_id: str, connection_id: str) -> Dict[str, Any]:
        """Remove a connection: clear its secret, disable its models, keep history.

        Authorises: one transaction that stamps ``deleted_at``, sets the four
        credential columns to NULL, and disables every model on that connection in
        this workspace. The row itself stays, so an old job's
        ``connection_id`` still resolves to a name.

        Refuses: a connection that is missing, already deleted, or in another
        workspace (``NOT_FOUND``). It never deletes ``generation_jobs`` rows and
        never modifies them -- deleting history would make a bill unexplainable --
        so the returned ``jobs_preserved`` is the number of jobs still pointing at
        the connection, and ``models_disabled`` is the number of model rows whose
        ``is_enabled`` actually changed.
        """
        scope = _require_workspace(workspace_id)
        self.get(scope, connection_id)
        deleted_at = now_iso()
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {
                    "deleted_at": deleted_at,
                    "is_enabled": 0,
                    "credential_ciphertext": None,
                    "credential_key_id": None,
                    "credential_last4": None,
                    "credential_fingerprint": None,
                    "updated_at": deleted_at,
                },
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (connection_id, scope),
            )
            models_disabled = self._db.update_where(
                "models",
                {"is_enabled": 0, "is_default": 0},
                "connection_id = ? AND workspace_id = ? AND is_enabled = 1",
                (connection_id, scope),
            )
            jobs_preserved = int(
                tx.scalar(
                    "SELECT COUNT(*) FROM generation_jobs"
                    " WHERE connection_id = ? AND workspace_id = ?",
                    (connection_id, scope),
                    default=0,
                )
                or 0
            )
        return {"models_disabled": int(models_disabled or 0), "jobs_preserved": jobs_preserved}


#: Payload keys accepted by :meth:`ModelRepository.create`.
_MODEL_FIELDS: Tuple[str, ...] = (
    "model_key",
    "display_name",
    "provider_slug",
    "modality",
    "modes",
    "capabilities",
    "parameter_schema",
    "defaults",
    "limits",
    "source",
    "is_enabled",
    "connection_id",
)

#: Fields :meth:`ModelRepository.update` may change. ``is_favorite`` and
#: ``is_default`` are excluded because they have their own methods that keep the
#: per-modality default invariant.
_MODEL_UPDATE_FIELDS: Tuple[str, ...] = (
    "model_key",
    "display_name",
    "provider_slug",
    "modality",
    "modes",
    "capabilities",
    "parameter_schema",
    "defaults",
    "limits",
    "source",
    "is_enabled",
    "connection_id",
)


class ModelRepository(_Repository):
    """The model registry: what each connection offers, and what it can do.

    Capability is stored, not inferred. A model that has not declared a mode
    cannot be asked for it, and :meth:`create` refuses a mode the modality does
    not support rather than letting the studio offer a button that fails later.
    """

    TABLE = "models"

    # -- reads --------------------------------------------------------------
    def list(
        self,
        workspace_id: str,
        *,
        modality: Optional[str] = None,
        connection_id: Optional[str] = None,
        source: Optional[str] = None,
        favorite: Optional[bool] = None,
        enabled: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Models in one workspace, optionally filtered.

        Authorises: reading the registry of the given workspace, with the
        connection's name and adapter type joined in so a caller can label a mock
        without a second query.

        Refuses: to include a model from another workspace, ever. An unknown
        ``modality`` or ``source` value is refused rather than silently matching
        nothing, because a typo would look like an empty workspace. ``q`` is
        matched against the display name and the wire key with LIKE wildcards
        escaped, so a user typing ``%`` searches for a percent sign.
        """
        scope = _require_workspace(workspace_id)
        clauses = ["m.workspace_id = ?"]
        params: List[Any] = [scope]
        if modality is not None:
            clauses.append("m.modality = ?")
            params.append(_choice(modality, MODEL_MODALITIES, "modality"))
        if connection_id is not None:
            clauses.append("m.connection_id = ?")
            params.append(_require_text(connection_id, "connection_id"))
        if source is not None:
            clauses.append("m.source = ?")
            params.append(_choice(source, MODEL_SOURCES, "source"))
        if favorite is not None:
            clauses.append("m.is_favorite = ?")
            params.append(1 if _flag(favorite) else 0)
        if enabled is not None:
            clauses.append("m.is_enabled = ?")
            params.append(1 if _flag(enabled) else 0)
        if q is not None and _text(q):
            pattern = _like(_text(q) or "")
            clauses.append("(m.display_name LIKE ? ESCAPE '\\' OR m.model_key LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        sql = (
            "SELECT " + self._projection("models", "m") + ","
            " c.name AS connection_name, c.adapter_type AS connection_adapter_type,"
            " c.is_enabled AS connection_is_enabled, c.deleted_at AS connection_deleted_at"
            " FROM models m LEFT JOIN api_connections c ON c.id = m.connection_id"
            " WHERE " + " AND ".join(clauses) +
            " ORDER BY m.is_default DESC, m.is_favorite DESC,"
            " m.display_name COLLATE NOCASE ASC"
        )
        out: List[Dict[str, Any]] = []
        for row in rows_to_dicts(self._db.query(sql, params)):
            shaped = _shape_row(self.TABLE, row) or {}
            shaped["connection_is_enabled"] = _int_flag(row.get("connection_is_enabled"))
            shaped["connection_deleted"] = row.get("connection_deleted_at") is not None
            out.append(shaped)
        return out

    def get(self, workspace_id: str, model_id: str) -> Dict[str, Any]:
        """One model, inside one workspace.

        Authorises: reading a model the workspace owns.

        Refuses: a model that does not exist or belongs to another workspace
        (``NOT_FOUND``, identically), and an empty model id.
        """
        scope = _require_workspace(workspace_id)
        row = self._db.query_dict(
            "SELECT " + self._projection("models", "m") + ","
            " c.name AS connection_name, c.adapter_type AS connection_adapter_type,"
            " c.is_enabled AS connection_is_enabled, c.deleted_at AS connection_deleted_at"
            " FROM models m LEFT JOIN api_connections c ON c.id = m.connection_id"
            " WHERE m.id = ? AND m.workspace_id = ?",
            (model_id, scope),
        )
        if row is None:
            raise _not_found("model")
        shaped = _shape_row(self.TABLE, row) or {}
        shaped["connection_is_enabled"] = _int_flag(row.get("connection_is_enabled"))
        shaped["connection_deleted"] = row.get("connection_deleted_at") is not None
        return shaped

    def find_by_key(
        self, workspace_id: str, connection_id: Optional[str], model_key: str
    ) -> Optional[Dict[str, Any]]:
        """The model a provider key maps to on one connection, or ``None``.

        Authorises: looking a model up by the identifier the provider uses, scoped
        to the workspace and the connection. This is what makes discovery
        idempotent: a re-run updates the row it already made instead of creating a
        duplicate.

        Refuses: to see another workspace's model. Returns ``None`` rather than
        raising, because "not registered yet" is the expected answer on a first
        discovery run and is not an error.
        """
        scope = _require_workspace(workspace_id)
        key = _require_text(model_key, "model_key")
        if connection_id is None:
            row = self._db.query_dict(
                "SELECT " + self._projection("models", "m") +
                " FROM models m WHERE m.workspace_id = ? AND m.connection_id IS NULL"
                " AND m.model_key = ?",
                (scope, key),
            )
        else:
            row = self._db.query_dict(
                "SELECT " + self._projection("models", "m") +
                " FROM models m WHERE m.workspace_id = ? AND m.connection_id = ?"
                " AND m.model_key = ?",
                (scope, _require_text(connection_id, "connection_id"), key),
            )
        return _shape_row(self.TABLE, row) if row is not None else None

    # -- writes -------------------------------------------------------------
    def create(self, workspace_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Register a model against one of this workspace's connections.

        Authorises: inserting a model into the given workspace, provided its
        connection is a live connection of that same workspace.

        Refuses: an unknown field; a missing model key; a modality outside
        image/video/other; a source outside discovered/manual; and -- the check
        that matters most -- a mode that does not belong to the modality, which
        raises ``CAPABILITY_UNSUPPORTED`` naming the mode and the vocabulary it
        would have needed to come from. Also refuses a connection id that is not a
        live connection in this workspace (``NOT_FOUND``) and a duplicate
        (connection, model_key) pair (``CONFLICT``).
        """
        scope = _require_workspace(workspace_id)
        values, row_id, _unused_owner = _absorb_row(
            _mapping(payload, "model"), _MODEL_ALIASES, "model", scope=scope
        )
        _reject_unknown(values, _MODEL_FIELDS, "model")

        model_key = _require_text(values.get("model_key"), "model_key", limit=200)
        display_name = _text(values.get("display_name")) or model_key
        modality = _choice(values.get("modality"), MODEL_MODALITIES, "modality")
        modes = _mode_list(values.get("modes"))
        _check_modes(modality, modes)
        source = _choice(values.get("source"), MODEL_SOURCES, "source", default="manual")

        connection_id: Optional[str] = None
        if values.get("connection_id") is not None:
            connection_id = _require_text(values.get("connection_id"), "connection_id")
            exists = self._db.exists(
                "api_connections",
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (connection_id, scope),
            )
            if not exists:
                raise _not_found("connection")

        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "connection_id": connection_id,
            "model_key": model_key,
            "display_name": display_name,
            "provider_slug": _text(values.get("provider_slug")) or "custom",
            "modality": modality,
            "modes_json": json_dumps(modes),
            "capabilities_json": json_dumps(_json_payload(values.get("capabilities"), "capabilities")),
            "parameter_schema_json": json_dumps(
                _json_payload(values.get("parameter_schema"), "parameter_schema")
            ),
            "defaults_json": json_dumps(_json_payload(values.get("defaults"), "defaults")),
            "limits_json": json_dumps(_json_payload(values.get("limits"), "limits")),
            "source": source,
            "is_enabled": 1 if _flag(values.get("is_enabled"), True) else 0,
        }
        try:
            with self._db.write_tx() as tx:
                inserted = tx.insert(self.TABLE, record)
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "model key on that connection") from error
        return self.get(scope, inserted["id"])

    def update(
        self, workspace_id: str, model_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a model's declared identity or capability.

        Authorises: updating the model key, display name, provider slug, modality,
        modes, capabilities, parameter schema, defaults, limits, source, enabled
        flag and connection -- for a model in this workspace.

        Refuses: an unknown field; ``is_favorite`` or ``is_default`` (those go
        through :meth:`set_favorite` and :meth:`set_default`, which maintain the
        per-modality invariant); a modality change that leaves the existing modes
        out of vocabulary, or a new mode that does not belong to the modality
        (``CAPABILITY_UNSUPPORTED`` naming it); a connection that is not a live
        connection of this workspace (``NOT_FOUND``); and a model that is missing
        or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        values, _row, _owner = _absorb_row(
            _mapping(fields, "model"), _MODEL_ALIASES, "model", scope=scope
        )
        if "is_favorite" in values or "is_default" in values:
            raise ApiError(
                VALIDATION_FAILED,
                "is_favorite and is_default have their own methods; they are not plain fields.",
                field="is_favorite" if "is_favorite" in values else "is_default",
            )
        _reject_unknown(values, _MODEL_UPDATE_FIELDS, "model")
        current = self.get(scope, model_id)

        updates: Dict[str, Any] = {}
        modality = current["modality"]
        if "modality" in values:
            modality = _choice(values.get("modality"), MODEL_MODALITIES, "modality")
            updates["modality"] = modality
        if "modes" in values:
            modes = _mode_list(values.get("modes"))
            _check_modes(modality, modes)
            updates["modes_json"] = json_dumps(modes)
        elif "modality" in values:
            # The modality moved, so the modes that were valid before must be
            # re-checked against the new vocabulary. A model whose stored modes no
            # longer fit its modality is a capability lie, not a cosmetic issue.
            _check_modes(modality, list(current.get("modes_json") or []))
        if "model_key" in values:
            updates["model_key"] = _require_text(values.get("model_key"), "model_key", limit=200)
        if "display_name" in values:
            updates["display_name"] = _require_text(values.get("display_name"), "display_name")
        if "provider_slug" in values:
            updates["provider_slug"] = _text(values.get("provider_slug")) or "custom"
        if "capabilities" in values:
            updates["capabilities_json"] = json_dumps(
                _json_payload(values.get("capabilities"), "capabilities")
            )
        if "parameter_schema" in values:
            updates["parameter_schema_json"] = json_dumps(
                _json_payload(values.get("parameter_schema"), "parameter_schema")
            )
        if "defaults" in values:
            updates["defaults_json"] = json_dumps(_json_payload(values.get("defaults"), "defaults"))
        if "limits" in values:
            updates["limits_json"] = json_dumps(_json_payload(values.get("limits"), "limits"))
        if "source" in values:
            updates["source"] = _choice(values.get("source"), MODEL_SOURCES, "source")
        if "is_enabled" in values:
            updates["is_enabled"] = 1 if _flag(values.get("is_enabled"), True) else 0
        if "connection_id" in values:
            connection_id = _text(values.get("connection_id"))
            if connection_id is not None:
                exists = self._db.exists(
                    "api_connections",
                    "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                    (connection_id, scope),
                )
                if not exists:
                    raise _not_found("connection")
            updates["connection_id"] = connection_id
        if not updates:
            return current

        try:
            with self._db.write_tx() as tx:
                self._db.update_where(
                    self.TABLE,
                    updates,
                    "id = ? AND workspace_id = ?",
                    (model_id, scope),
                )
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "model key on that connection") from error
        return self.get(scope, model_id)

    def set_favorite(self, workspace_id: str, model_id: str, favorite: bool = True) -> Dict[str, Any]:
        """Mark a model as a favourite for the workspace.

        Authorises: flipping ``is_favorite`` on a model in this workspace. A
        favourite is a UI shortcut only; it grants no capability and changes no
        default.

        Refuses: a model that is missing or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        self.get(scope, model_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"is_favorite": 1 if favorite else 0},
                "id = ? AND workspace_id = ?",
                (model_id, scope),
            )
        return self.get(scope, model_id)

    def set_default(self, workspace_id: str, model_id: str) -> Dict[str, Any]:
        """Make one model the default for its modality, clearing the others.

        Authorises: one transaction that unsets ``is_default`` on every other
        model of the same modality in this workspace and sets it on this one. The
        transaction matters: two concurrent requests cannot both leave a
        workspace with two defaults, and a failure cannot leave it with none.

        Refuses: a model that is missing or foreign (``NOT_FOUND``), and a
        disabled model -- promoting a model nobody can use would make the studio
        open on a dead default.
        """
        scope = _require_workspace(workspace_id)
        current = self.get(scope, model_id)
        if not current.get("is_enabled"):
            raise ApiError(
                CONFLICT,
                "A disabled model cannot be the default for its modality.",
                hint="Enable the model first.",
            )
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"is_default": 0},
                "workspace_id = ? AND modality = ? AND id <> ? AND is_default = 1",
                (scope, current["modality"], model_id),
            )
            self._db.update_where(
                self.TABLE,
                {"is_default": 1},
                "id = ? AND workspace_id = ?",
                (model_id, scope),
            )
        return self.get(scope, model_id)

    def delete(self, workspace_id: str, model_id: str) -> None:
        """Remove a model from the registry.

        Authorises: hard-deleting a model row in this workspace. ``models`` has no
        ``deleted_at``, and a model carries no history of its own; the jobs that
        used it keep their own snapshot of the request and lose only this
        reference (``ON DELETE SET NULL``).

        Refuses: a model that is missing or foreign (``NOT_FOUND``). It does not
        touch ``generation_jobs`` or ``assets``; deleting a model must not delete
        work that was made with it.
        """
        scope = _require_workspace(workspace_id)
        with self._db.write_tx() as tx:
            removed = tx.execute(
                "DELETE FROM models WHERE id = ? AND workspace_id = ?", (model_id, scope)
            ).rowcount
        if not removed:
            raise _not_found("model")

    def with_provider(self, workspace_id: str, model_id: str) -> Dict[str, Any]:
        """A model plus everything needed to route a request to its provider.

        Authorises: reading the model and its connection's name, adapter type,
        base URL and enabled flag for a model in this workspace -- the tuple the
        job runner needs before it may spend money.

        Refuses: a model that is missing or foreign (``NOT_FOUND``). ``is_mock``
        is *derived* by asking the adapter registry, never stored: a column could
        disagree with the code, and the derived value cannot. An adapter type with
        no registered adapter yields ``is_mock=False`` and ``adapter_missing=True`
        inside the same try/except, so a stale row cannot crash the caller -- but
        it is also not silently described as a real provider.
        """
        scope = _require_workspace(workspace_id)
        model = self.get(scope, model_id)
        adapter_type = model.get("connection_adapter_type")
        is_mock = False
        adapter_missing = False
        if adapter_type:
            adapter = _adapter_for(str(adapter_type))
            adapter_missing = adapter is None
            is_mock = bool(getattr(adapter, "is_mock", False))
        model["adapter_type"] = adapter_type
        model["provider_name"] = model.get("connection_name")
        model["base_url"] = None
        model["provider_enabled"] = bool(model.get("connection_is_enabled"))
        if model.get("connection_id"):
            base_url = self._db.scalar(
                "SELECT base_url FROM api_connections WHERE id = ? AND workspace_id = ?",
                (model["connection_id"], scope),
            )
            model["base_url"] = base_url
        model["is_mock"] = is_mock
        model["adapter_missing"] = adapter_missing
        return model


#: Adapter modules that register themselves on import. Importing them by name
#: rather than importing :mod:`studio.adapters` keeps this module's import cost
#: off the hot path while still making the registry complete before a lookup.
_ADAPTER_MODULES: Tuple[str, ...] = (
    "studio.adapters.declarative",
    "studio.adapters.fal",
    "studio.adapters.mock",
    "studio.adapters.openai",
)


def _adapter_for(adapter_type: str) -> Optional[Any]:
    """The adapter singleton for a stored ``adapter_type``, or ``None``.

    The registry in :mod:`studio.adapters.core` is populated by the adapter
    modules' import side effects, so a module that only imported ``core`` would
    find an empty registry and conclude -- wrongly -- that a real adapter is
    missing. This makes the import explicit and idempotent, then reports a genuine
    absence as ``None`` instead of raising: a stale row in the models table must
    not be able to break a read.
    """
    for name in _ADAPTER_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - a missing optional dependency is not fatal here
            continue
    try:
        return get_adapter(adapter_type)
    except Exception:  # noqa: BLE001 - an unknown adapter is a data problem, not a crash
        return None


def _get(source: Mapping[str, Any], *names: str) -> Any:
    """The first present, non-None value among the given keys."""
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return None


def _json_payload(value: Any, field: str) -> Any:
    """Accept a dict/list or a JSON string for a JSON column.

    Callers arrive from two directions: an HTTP handler with a parsed object, and
    a worker with a string it just read out of the database. Both are legitimate
    and neither should have to convert.
    """
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        parsed = json_loads(value, default=_Missing)
        if parsed is _Missing:
            raise ApiError(
                VALIDATION_FAILED,
                "%s must be valid JSON." % field.replace("_", " ").capitalize(),
                field=field,
            )
        return parsed
    raise ApiError(
        VALIDATION_FAILED,
        "%s must be a JSON object or a JSON string." % field.replace("_", " ").capitalize(),
        field=field,
    )


class _MissingType:
    """Sentinel for "the JSON did not parse", distinct from a parsed ``None``."""

    __slots__ = ()


_Missing = _MissingType()


def _header_map(value: Any) -> Dict[str, str]:
    """Non-secret headers, with the secret-bearing names refused.

    The column is documented as a place for a project id or an organization id.
    Letting ``Authorization`` sit in it would create a second, unencrypted key
    store, so the names that carry credentials are rejected outright rather than
    encrypted after the fact.
    """
    payload = _json_payload(value, "extra_headers")
    if not isinstance(payload, dict):
        raise ApiError(
            VALIDATION_FAILED,
            "extra_headers must be an object of header names to values.",
            field="extra_headers",
        )
    headers: Dict[str, str] = {}
    for key, item in payload.items():
        name = _text(key)
        if not name:
            continue
        if name.strip().lower() in _SECRET_HEADER_KEYS:
            raise ApiError(
                VALIDATION_FAILED,
                "'%s' is a credential header and cannot be stored in extra_headers." % name,
                field="extra_headers",
                hint="Put the key in the connection credential instead, where it is sealed.",
            )
        headers[name] = truncate(str(item), 1024)
    return headers


def _host_list(value: Any) -> List[str]:
    """Normalise the allowed-hosts list to bare, lower-case host names."""
    if value is None:
        return []
    if isinstance(value, str):
        items: List[Any] = [part for part in value.replace(",", " ").split()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ApiError(
            VALIDATION_FAILED,
            "allowed_hosts must be a list of host names.",
            field="allowed_hosts",
        )
    hosts: List[str] = []
    for item in items:
        host = _text(item)
        if not host:
            continue
        host = host.strip().lower()
        if "://" in host:
            parsed = urlsplit(host)
            if not parsed.hostname:
                raise ApiError(
                    VALIDATION_FAILED,
                    "'%s' is not a host name." % host,
                    field="allowed_hosts",
                )
            host = parsed.hostname
        if host not in hosts:
            hosts.append(host)
    return hosts


# ---------------------------------------------------------------------------
# Projects and assets
# ---------------------------------------------------------------------------

#: Payload keys accepted by :meth:`ProjectRepository.create`.
_PROJECT_FIELDS: Tuple[str, ...] = (
    "name",
    "description",
    "cover_asset_id",
    "status",
    "tags",
)


class ProjectRepository(_Repository):
    """Projects: a named container over assets the library still owns.

    Membership is by asset id (``project_assets``), so attaching an asset to a
    project never moves it out of the library that holds it, and detaching never
    deletes anything.
    """

    TABLE = "projects"

    # -- reads --------------------------------------------------------------
    def list(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        include_archived: bool = True,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Live projects in one workspace, with their asset counts.

        Authorises: reading project metadata and the number of attached, live
        assets for the given workspace.

        Refuses: soft-deleted projects (they are reported as gone everywhere) and
        any project from another workspace. An unknown ``status`` value is
        refused rather than treated as "no filter".
        """
        scope = _require_workspace(workspace_id)
        clauses = ["p.workspace_id = ?", "p.deleted_at IS NULL"]
        params: List[Any] = [scope]
        if status is not None:
            clauses.append("p.status = ?")
            params.append(_choice(status, PROJECT_STATUSES, "status"))
        elif not include_archived:
            clauses.append("p.status = 'active'")
        if q is not None and _text(q):
            pattern = _like(_text(q) or "")
            clauses.append("(p.name LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        sql = (
            "SELECT " + self._projection("projects", "p") + ","
            " (SELECT COUNT(*) FROM project_assets pa JOIN assets a ON a.id = pa.asset_id"
            "   WHERE pa.project_id = p.id AND a.deleted_at IS NULL) AS asset_count,"
            " (SELECT COUNT(*) FROM generation_jobs j WHERE j.project_id = p.id) AS job_count"
            " FROM projects p WHERE " + " AND ".join(clauses) +
            " ORDER BY p.updated_at DESC, p.name COLLATE NOCASE ASC"
        )
        return self._all(sql, params)

    def get(self, workspace_id: str, project_id: str) -> Dict[str, Any]:
        """One project with its attached assets.

        Authorises: reading a project and the live assets attached to it, for a
        project in this workspace.

        Refuses: a project that is missing, soft-deleted, or in another workspace
        (``NOT_FOUND``, identically). Only assets in the same workspace are
        included, so a cross-workspace ``project_assets`` row -- which the schema
        does not prevent -- cannot smuggle a foreign asset into the response.
        """
        scope = _require_workspace(workspace_id)
        row = self._db.query_dict(
            "SELECT " + self._projection("projects", "p") +
            " FROM projects p WHERE p.id = ? AND p.workspace_id = ? AND p.deleted_at IS NULL",
            (project_id, scope),
        )
        if row is None:
            raise _not_found("project")
        project = _shape_row(self.TABLE, row) or {}
        assets = rows_to_dicts(
            self._db.query(
                "SELECT a.id, a.kind, a.source, a.filename, a.mime_type, a.byte_size,"
                " a.thumbnail_key, a.storage_key, a.width, a.height, a.is_favorite,"
                " pa.added_at"
                " FROM project_assets pa JOIN assets a ON a.id = pa.asset_id"
                " WHERE pa.project_id = ? AND a.workspace_id = ? AND a.deleted_at IS NULL"
                " ORDER BY pa.added_at DESC",
                (project_id, scope),
            )
        )
        for asset in assets:
            asset["is_favorite"] = _int_flag(asset.get("is_favorite"))
            asset["added_at"] = _iso(asset.get("added_at"))
        project["assets"] = assets
        project["asset_count"] = len(assets)
        return project

    # -- writes -------------------------------------------------------------
    def create(
        self, workspace_id: str, user_id: str, payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Create a project in one workspace.

        Authorises: inserting a project attributed to ``user_id``; a cover asset
        may be named only if it is a live asset of this same workspace.

        Refuses: an unknown field, a missing name, a status outside
        active/archived, and a cover asset id that is not a live asset here
        (``NOT_FOUND``, which also covers the case of an id from another
        workspace).
        """
        scope = _require_workspace(workspace_id)
        owner = _require_text(user_id, "user_id")
        values, row_id, _owner = _absorb_row(
            _mapping(payload, "project"), _LIBRARY_ALIASES, "project", scope=scope
        )
        _reject_unknown(values, _PROJECT_FIELDS, "project")
        name = _require_text(values.get("name"), "name", limit=200)
        cover = _text(values.get("cover_asset_id"))
        if cover is not None and not self._db.exists(
            "assets", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (cover, scope)
        ):
            raise _not_found("asset")
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "name": name,
            "description": truncate(str(values.get("description") or ""), 4000) or "",
            "cover_asset_id": cover,
            "status": _choice(values.get("status"), PROJECT_STATUSES, "status", default="active"),
            "tags_json": json_dumps(_tag_list(values.get("tags"))),
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert(self.TABLE, record)
        return self.get(scope, inserted["id"])

    def update(
        self, workspace_id: str, project_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a project's name, description, cover, status or tags.

        Authorises: updating those five fields on a live project in this
        workspace.

        Refuses: an unknown field, ``workspace_id``, ``deleted_at`, an empty
        name, an invalid status, a cover asset from another workspace
        (``NOT_FOUND``), and a project that is missing or foreign
        (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        values = _mapping(fields, "project")
        _reject_unknown(values, _PROJECT_FIELDS, "project")
        self.get(scope, project_id)
        updates: Dict[str, Any] = {}
        if "name" in values:
            updates["name"] = _require_text(values.get("name"), "name", limit=200)
        if "description" in values:
            updates["description"] = truncate(str(values.get("description") or ""), 4000) or ""
        if "status" in values:
            updates["status"] = _choice(values.get("status"), PROJECT_STATUSES, "status")
        if "tags" in values:
            updates["tags_json"] = json_dumps(_tag_list(values.get("tags")))
        if "cover_asset_id" in values:
            cover = _text(values.get("cover_asset_id"))
            if cover is not None and not self._db.exists(
                "assets", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (cover, scope)
            ):
                raise _not_found("asset")
            updates["cover_asset_id"] = cover
        if not updates:
            return self.get(scope, project_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                updates,
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (project_id, scope),
            )
        return self.get(scope, project_id)

    def soft_delete(self, workspace_id: str, project_id: str) -> Dict[str, Any]:
        """Remove a project without removing the work in it.

        Authorises: stamping ``deleted_at``, clearing the cover, and detaching
        every asset from the project, all in one transaction.

        Refuses: a project that is missing, already deleted, or foreign
        (``NOT_FOUND``). It never deletes an asset: the assets go back to living
        in the library alone, which is why the return value reports
        ``assets_unassigned`` -- the number of asset rows that were still
        attached and are now not.
        """
        scope = _require_workspace(workspace_id)
        self.get(scope, project_id)
        with self._db.write_tx() as tx:
            assets_unassigned = int(
                tx.scalar(
                    "SELECT COUNT(*) FROM project_assets pa JOIN assets a ON a.id = pa.asset_id"
                    " WHERE pa.project_id = ? AND a.workspace_id = ? AND a.deleted_at IS NULL",
                    (project_id, scope),
                    default=0,
                )
                or 0
            )
            tx.execute("DELETE FROM project_assets WHERE project_id = ?", (project_id,))
            self._db.update_where(
                "assets",
                {"project_id": None},
                "project_id = ? AND workspace_id = ?",
                (project_id, scope),
            )
            self._db.update_where(
                self.TABLE,
                {"deleted_at": now_iso(), "cover_asset_id": None},
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (project_id, scope),
            )
        return {"assets_unassigned": assets_unassigned}

    def attach_assets(
        self, workspace_id: str, project_id: str, asset_ids: Sequence[str]
    ) -> Dict[str, Any]:
        """Attach assets to a project, honouring workspace ownership.

        Authorises: inserting ``project_assets`` rows for the subset of
        ``asset_ids`` that are live assets of this workspace, and setting their
        ``project_id`` so the library can filter by project.

        Refuses: ids from another workspace or that no longer exist -- they are
        reported in ``skipped`` rather than attached, and the call does *not*
        raise, because a batch that names one stale id should still attach the
        rest. Refuses an unknown project (``NOT_FOUND``) and an empty id list.
        """
        scope = _require_workspace(workspace_id)
        project = self.get(scope, project_id)  # 404 before anything is written
        wanted = [str(item) for item in (asset_ids or []) if _text(item)]
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            return {"project_id": project["id"], "attached": [], "skipped": []}
        rows = rows_to_dicts(
            self._db.query(
                "SELECT id FROM assets WHERE workspace_id = ? AND deleted_at IS NULL"
                " AND id IN (" + _placeholders(len(wanted)) + ")",
                [scope] + wanted,
            )
        )
        owned = {row["id"] for row in rows}
        attached = [asset_id for asset_id in wanted if asset_id in owned]
        skipped = [asset_id for asset_id in wanted if asset_id not in owned]
        stamp = now_iso()
        with self._db.write_tx() as tx:
            for asset_id in attached:
                tx.execute(
                    "INSERT OR IGNORE INTO project_assets (project_id, asset_id, added_at)"
                    " VALUES (?, ?, ?)",
                    (project["id"], asset_id, stamp),
                )
                self._db.update_where(
                    "assets",
                    {"project_id": project["id"]},
                    "id = ? AND workspace_id = ?",
                    (asset_id, scope),
                )
        return {"project_id": project["id"], "attached": attached, "skipped": skipped}


def _tag_list(value: Any) -> List[str]:
    """Normalise a tag payload to a list of short, unique, non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        items: List[Any] = [part for part in value.replace(",", " ").split()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ApiError(VALIDATION_FAILED, "tags must be a list of strings.", field="tags")
    tags: List[str] = []
    for item in items:
        tag = _text(item)
        if tag and tag not in tags:
            tags.append(truncate(tag, 64))
    return tags


#: Payload keys accepted by :meth:`AssetRepository.create`.
_ASSET_FIELDS: Tuple[str, ...] = (
    "kind",
    "source",
    "storage_key",
    "thumbnail_key",
    "filename",
    "mime_type",
    "byte_size",
    "checksum",
    "width",
    "height",
    "duration_seconds",
    "frame_rate",
    "reference_role",
    "metadata",
    "parent_asset_id",
    "project_id",
    "is_favorite",
)


class AssetRepository(_Repository):
    """The asset index. Bytes live in object storage; this is what the UI reads.

    Nothing here reads or writes storage: a repository that could fetch a file
    would be a repository that can block, fail, and be abused for a URL fetch.
    """

    TABLE = "assets"

    # -- reads --------------------------------------------------------------
    def list(
        self,
        workspace_id: str,
        *,
        kind: Optional[str] = None,
        source: Optional[str] = None,
        project_id: Optional[str] = None,
        favorite: Optional[bool] = None,
        q: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Live assets in one workspace, newest first, optionally filtered.

        Returns ``(rows, next_cursor)``: a page of assets plus an opaque cursor
        for the page that follows. ``next_cursor`` is ``None`` on the last page,
        so a client never has to ask for an empty page to learn it is done.

        Authorises: reading asset metadata for the given workspace, paged with the
        same keyset cursor :meth:`JobRepository.list` uses (``created_at, id``).

        Refuses: assets from another workspace and soft-deleted assets; an unknown
        ``kind`` or ``source``; and a ``limit`` outside 1..200, clamped rather
        than trusted, so a huge page cannot be requested by accident.
        """
        scope = _require_workspace(workspace_id)
        clauses = ["a.workspace_id = ?", "a.deleted_at IS NULL"]
        params: List[Any] = [scope]
        if kind is not None:
            clauses.append("a.kind = ?")
            params.append(_choice(kind, ASSET_KINDS, "kind"))
        if source is not None:
            clauses.append("a.source = ?")
            params.append(_choice(source, ASSET_SOURCES, "source"))
        if project_id is not None:
            clauses.append("a.project_id = ?")
            params.append(_require_text(project_id, "project_id"))
        if favorite is not None:
            clauses.append("a.is_favorite = ?")
            params.append(1 if _flag(favorite) else 0)
        if q is not None and _text(q):
            pattern = _like(_text(q) or "")
            clauses.append("a.filename LIKE ? ESCAPE '\\'")
            params.append(pattern)
        cursor_key = _decode_cursor(cursor)
        if cursor_key is not None:
            clauses.append("(a.created_at < ? OR (a.created_at = ? AND a.id < ?))")
            params.extend([cursor_key[0], cursor_key[0], cursor_key[1]])
        page_size = int(clamp(int(limit), 1, _MAX_LIMIT))
        params.append(page_size + 1)
        sql = (
            "SELECT " + self._projection("assets", "a") + " FROM assets a WHERE "
            + " AND ".join(clauses)
            + " ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
        )
        rows = self._all(sql, params)
        next_cursor: Optional[str] = None
        if len(rows) > page_size:
            rows = rows[:page_size]
            last = rows[-1]
            next_cursor = _cursor_key(last["created_at"], last["id"])
        return rows, next_cursor

    def get(self, workspace_id: str, asset_id: str) -> Dict[str, Any]:
        """One live asset, inside one workspace.

        Authorises: reading an asset's metadata.

        Refuses: an asset that is missing, soft-deleted, or in another workspace
        (``NOT_FOUND``, identically).
        """
        scope = _require_workspace(workspace_id)
        return self._scoped_row(
            "SELECT " + self._projection("assets", "a") + " FROM assets a"
            " WHERE a.id = ? AND a.workspace_id = ? AND a.deleted_at IS NULL",
            (asset_id, scope),
            "asset",
        )

    def by_ids(self, workspace_id: str, asset_ids: Sequence[str]) -> List[Dict[str, Any]]:
        """The assets among ``asset_ids`` that live in this workspace.

        Authorises: resolving a batch of ids -- typically a job's references -- to
        the rows this workspace may actually use.

        Refuses: ids belonging to another workspace, and soft-deleted rows; they
        are simply absent from the result. This is the safe bulk lookup: a caller
        must treat a missing id as unusable rather than assuming it was fetched.
        """
        scope = _require_workspace(workspace_id)
        wanted = [str(item) for item in (asset_ids or []) if _text(item)]
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            return []
        sql = (
            "SELECT " + self._projection("assets", "a") + " FROM assets a"
            " WHERE a.workspace_id = ? AND a.deleted_at IS NULL"
            " AND a.id IN (" + _placeholders(len(wanted)) + ")"
            " ORDER BY a.created_at DESC"
        )
        return self._all(sql, [scope] + wanted)

    def count_for_project(self, workspace_id: str, project_id: str) -> int:
        """How many live assets this workspace has in one project.

        Authorises: counting, never reading, the assets attached to a project.

        Refuses: to count anything outside this workspace. Refuses an unknown
        project (``NOT_FOUND``), because returning 0 for someone else's project id
        would answer "does that project exist?" -- exactly the probe the not-found
        rule exists to prevent.
        """
        scope = _require_workspace(workspace_id)
        exists = self._db.exists(
            "projects", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (project_id, scope)
        )
        if not exists:
            raise _not_found("project")
        return self._count(
            "SELECT COUNT(*) FROM assets a WHERE a.project_id = ? AND a.workspace_id = ?"
            " AND a.deleted_at IS NULL",
            (project_id, scope),
        )

    # -- writes -------------------------------------------------------------
    def create(
        self,
        workspace_id: str,
        user_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Index a stored file as an asset.

        Authorises: inserting an asset row attributed to ``user_id``, with a
        parent asset and a project that must both belong to this workspace.

        Refuses: an unknown field; a kind outside image/video/audio/other; a
        source outside upload/generated/derived; a missing storage key or mime
        type; a parent or project id that is not a live row of this workspace
        (``NOT_FOUND``); and a negative byte size.

        Call shape: ``create(workspace_id, user_id, payload)`` or
        ``create(workspace_id, row)``, where the single mapping carries
        ``owner_user_id``. The storage service assembles a whole row and passes it
        whole; a route handler passes the owner and a request body. Both arrive at
        the same validation.
        """
        scope = _require_workspace(workspace_id)
        if payload is None and isinstance(user_id, Mapping):
            payload, user_id = user_id, None
        values, row_id, echoed_owner = _absorb_row(
            _mapping(payload, "asset"), _ASSET_ALIASES, "asset", scope=scope
        )
        # An explicit argument wins over the payload: the caller who authenticated
        # this request decided who owns the row, and a body cannot overrule it.
        explicit_owner = _text(user_id)
        owner = explicit_owner or echoed_owner
        if not owner:
            raise ApiError(
                VALIDATION_FAILED,
                "An owner is required to index an asset.",
                field="owner_user_id",
            )
        _reject_unknown(values, _ASSET_FIELDS, "asset")

        storage_key = _require_text(values.get("storage_key"), "storage_key", limit=500)
        mime_type = _require_text(values.get("mime_type"), "mime_type", limit=200)
        byte_size = _optional_int(values.get("byte_size")) or 0
        if byte_size < 0:
            raise ApiError(VALIDATION_FAILED, "byte_size cannot be negative.", field="byte_size")

        parent = _text(values.get("parent_asset_id"))
        if parent is not None and not self._db.exists(
            "assets", "id = ? AND workspace_id = ?", (parent, scope)
        ):
            raise _not_found("parent asset")
        project = _text(values.get("project_id"))
        if project is not None and not self._db.exists(
            "projects", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (project, scope)
        ):
            raise _not_found("project")

        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "owner_user_id": owner,
            "project_id": project,
            "kind": _choice(values.get("kind"), ASSET_KINDS, "kind"),
            "source": _choice(values.get("source"), ASSET_SOURCES, "source"),
            "storage_key": storage_key,
            "thumbnail_key": _text(values.get("thumbnail_key")),
            "filename": truncate(str(values.get("filename") or ""), 500) or "",
            "mime_type": mime_type,
            "byte_size": byte_size,
            "checksum": truncate(str(values.get("checksum") or ""), 200) or "",
            "width": _optional_int(values.get("width")),
            "height": _optional_int(values.get("height")),
            "duration_seconds": _optional_float(values.get("duration_seconds")),
            "frame_rate": _optional_float(values.get("frame_rate")),
            "reference_role": _text(values.get("reference_role")),
            "metadata_json": json_dumps(_json_payload(values.get("metadata"), "metadata")),
            "parent_asset_id": parent,
            "is_favorite": 1 if _flag(values.get("is_favorite"), False) else 0,
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert(self.TABLE, record)
        return self.get(scope, inserted["id"])

    def update(
        self, workspace_id: str, asset_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change an asset's descriptive metadata.

        Authorises: updating filename, reference role, metadata, dimensions, the
        project it belongs to and the favourite flag, for a live asset in this
        workspace.

        Refuses: an unknown field; ``storage_key``, ``checksum`` and ``source``
        -- the bytes are immutable through this layer, so a row cannot be pointed
        at different content while a job still refers to it; a project id from
        another workspace (``NOT_FOUND``); and an asset that is missing, deleted,
        or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        values, _row, _owner = _absorb_row(
            _mapping(fields, "asset"), _ASSET_ALIASES, "asset", scope=scope
        )
        _reject_unknown(values, _ASSET_FIELDS, "asset")
        for immutable in ("storage_key", "checksum", "source", "kind"):
            if immutable in values:
                raise ApiError(
                    VALIDATION_FAILED,
                    "%s cannot be changed after an asset is stored." % immutable,
                    field=immutable,
                )
        self.get(scope, asset_id)

        updates: Dict[str, Any] = {}
        if "filename" in values:
            updates["filename"] = truncate(str(values.get("filename") or ""), 500) or ""
        if "thumbnail_key" in values:
            updates["thumbnail_key"] = _text(values.get("thumbnail_key"))
        if "reference_role" in values:
            updates["reference_role"] = _text(values.get("reference_role"))
        if "metadata" in values:
            updates["metadata_json"] = json_dumps(_json_payload(values.get("metadata"), "metadata"))
        if "is_favorite" in values:
            updates["is_favorite"] = 1 if _flag(values.get("is_favorite"), False) else 0
        if "width" in values:
            updates["width"] = _optional_int(values.get("width"))
        if "height" in values:
            updates["height"] = _optional_int(values.get("height"))
        if "project_id" in values:
            project = _text(values.get("project_id"))
            if project is not None:
                if not self._db.exists(
                    "projects", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (project, scope)
                ):
                    raise _not_found("project")
                stamp = now_iso()
                with self._db.write_tx() as tx:
                    tx.execute(
                        "INSERT OR IGNORE INTO project_assets (project_id, asset_id, added_at)"
                        " VALUES (?, ?, ?)",
                        (project, asset_id, stamp),
                    )
            updates["project_id"] = project
        if not updates:
            return self.get(scope, asset_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                updates,
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (asset_id, scope),
            )
        return self.get(scope, asset_id)

    def soft_delete(self, workspace_id: str, asset_id: str) -> None:
        """Mark an asset deleted, keeping the row for provenance.

        Authorises: stamping ``deleted_at`` and clearing the project link, so the
        row stops appearing in the library and in project counts.

        Refuses: an asset that is missing, already deleted, or foreign
        (``NOT_FOUND``). It does not remove the bytes from storage -- that is a
        separate, audited retention step -- and it does not delete
        ``generation_outputs`` rows, because a job's record of what it produced
        must survive the asset going away.
        """
        scope = _require_workspace(workspace_id)
        self.get(scope, asset_id)
        with self._db.write_tx() as tx:
            tx.execute("DELETE FROM project_assets WHERE asset_id = ?", (asset_id,))
            self._db.update_where(
                self.TABLE,
                {"deleted_at": now_iso(), "project_id": None, "is_favorite": 0},
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (asset_id, scope),
            )


# ---------------------------------------------------------------------------
# The job queue
# ---------------------------------------------------------------------------

#: Payload keys accepted by :meth:`JobRepository.create`.
_JOB_FIELDS: Tuple[str, ...] = (
    "project_id",
    "connection_id",
    "model_id",
    "modality",
    "mode",
    "prompt",
    "negative_prompt",
    "parameters",
    "request",
    "references",
    "max_attempts",
    "cost_estimate",
    "idempotency_key",
    "status",
)

#: The default page size when a caller does not name one.
_DEFAULT_LIMIT = 50
#: The largest page this layer will build. A caller asking for more is clamped,
#: not refused: an oversized page is a performance question, not a security one.
_MAX_LIMIT = 200


def _cursor_key(created_at: str, row_id: str) -> str:
    """An opaque cursor over ``(created_at, id)``, base64 so it is obviously not an id.

    Keyset rather than OFFSET: a page taken while new jobs arrive does not shift,
    and a client cannot ask for page 10000 of a table it was never allowed to page.
    """
    import base64

    raw = json_dumps([str(created_at), str(row_id)]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> Optional[Tuple[str, str]]:
    """The ``(created_at, id)`` pair a cursor stands for, or ``None``.

    A cursor that cannot be decoded is refused outright rather than ignored: a
    silently-ignored cursor returns page one forever, which looks like a bug in
    the pagination and hides the real problem.
    """
    if cursor is None or cursor == "":
        return None
    import base64

    try:
        raw = base64.urlsafe_b64decode(str(cursor).encode("ascii"))
        decoded = json_loads(raw.decode("utf-8"), default=None)
    except (ValueError, UnicodeDecodeError):
        decoded = None
    if not isinstance(decoded, list) or len(decoded) != 2:
        raise ApiError(
            VALIDATION_FAILED,
            "That pagination cursor is not one this platform issued.",
            field="cursor",
        )
    return str(decoded[0]), str(decoded[1])


def _error_columns(error: Any) -> Dict[str, Any]:
    """The four ``error_*`` columns, from an ApiError, a dict, or a bare code.

    Nothing is invented: a failure recorded without a message gets the message
    that was actually given (which may be empty), and the provider's own words go
    in ``provider_error_json`` only when somebody supplied them, already scrubbed.
    """
    if error is None:
        return {}
    if isinstance(error, ApiError):
        return {
            "error_code": error.code,
            "error_message": truncate(error.message, 2000),
            "error_hint": truncate(error.hint, 2000) if error.hint else None,
            "provider_error_json": (
                json_dumps({"provider_message": error.provider_message})
                if error.provider_message
                else "{}"
            ),
        }
    if isinstance(error, Mapping):
        code = _text(error.get("code")) or "internal_error"
        provider_message = _text(error.get("provider_message"))
        return {
            "error_code": code,
            "error_message": truncate(str(error.get("message") or ""), 2000) or None,
            "error_hint": truncate(str(error.get("hint") or ""), 2000) or None,
            "provider_error_json": (
                json_dumps({"provider_message": provider_message})
                if provider_message
                else "{}"
            ),
        }
    return {
        "error_code": truncate(str(error), 100),
        "error_message": None,
        "error_hint": None,
        "provider_error_json": "{}",
    }


class JobRepository(_Repository):
    """The generation queue. A table with leases, not a broker.

    The invariant everything else rests on: a row's ``status`` is the state the job
    actually reached, never an intention. ``completed`` is written only after an
    output row exists, ``failed`` only with a diagnostic attached, and every move
    appends a ``job_events`` row in the same transaction so the history cannot
    disagree with the current state.

    Two callers use this class, and they are scoped differently on purpose. A
    request-scoped caller passes ``workspace_id`` to every method and cannot reach
    another tenant's job. The worker's queue methods (``claim_next``,
    ``release_lease``, ``retryable``, ``stale_jobs``) work by job id alone, because
    one worker drives every tenant's jobs -- each of those says so in its own
    docstring.
    """

    TABLE = "generation_jobs"

    # -- reads --------------------------------------------------------------
    def get(self, workspace_id: str, job_id: str) -> Dict[str, Any]:
        """One job, inside one workspace, with its events and outputs.

        Authorises: reading a job this workspace owns, plus the two append-only
        logs that belong to it.

        Refuses: a job that does not exist or belongs to another workspace
        (``NOT_FOUND``, identically, so a job id cannot be probed). The credential
        is never part of a job: a job carries ``connection_id`` and its own request
        snapshot, nothing more.
        """
        scope = _require_workspace(workspace_id)
        job = self._scoped_row(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (job_id, scope),
            "job",
        )
        job["events"] = self.events(job_id, workspace_id=scope)
        job["outputs"] = self.outputs(job_id, workspace_id=scope)
        return job

    def list(
        self,
        workspace_id: str,
        *,
        state: Optional[str] = None,
        modality: Optional[str] = None,
        project_id: Optional[str] = None,
        model_id: Optional[str] = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """A page of jobs, newest first, plus the cursor for the next page.

        Authorises: reading this workspace's job history, filtered by exact state
        (or the ``active``/``terminal`` buckets), modality, project or model.

        Refuses: jobs from another workspace; an unknown state, modality, or
        cursor; and a ``limit`` outside 1..200 (clamped, not trusted). The cursor
        returned is ``None`` when the page is the last one, so a client never has
        to ask for an empty page to learn it is done.
        """
        scope = _require_workspace(workspace_id)
        clauses = ["j.workspace_id = ?"]
        params: List[Any] = [scope]
        if state is not None:
            requested = _choice(state, JOB_STATES + tuple(sorted(_STATE_ALIASES)), "state")
            states = _STATE_ALIASES.get(requested, (requested,))
            clauses.append("j.status IN (" + _placeholders(len(states)) + ")")
            params.extend(states)
        if modality is not None:
            clauses.append("j.modality = ?")
            params.append(_choice(modality, JOB_MODALITIES, "modality"))
        if project_id is not None:
            clauses.append("j.project_id = ?")
            params.append(_require_text(project_id, "project_id"))
        if model_id is not None:
            clauses.append("j.model_id = ?")
            params.append(_require_text(model_id, "model_id"))
        cursor_key = _decode_cursor(cursor)
        if cursor_key is not None:
            clauses.append("(j.created_at < ? OR (j.created_at = ? AND j.id < ?))")
            params.extend([cursor_key[0], cursor_key[0], cursor_key[1]])
        page_size = int(clamp(int(limit), 1, _MAX_LIMIT))
        params.append(page_size + 1)
        sql = (
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
            " WHERE " + " AND ".join(clauses) +
            " ORDER BY j.created_at DESC, j.id DESC LIMIT ?"
        )
        rows = self._all(sql, params)
        next_cursor = None
        if len(rows) > page_size:
            rows = rows[:page_size]
            last = rows[-1]
            next_cursor = _cursor_key(last["created_at"], last["id"])
        return rows, next_cursor

    def counts(self, workspace_id: str) -> Dict[str, int]:
        """How many jobs sit in each bucket for one workspace.

        Authorises: counting this workspace's jobs by state.

        Refuses: to count across tenants. Returns the four buckets the dashboards
        ask for -- ``queued``, ``processing``, ``completed``, ``failed`` -- plus
        ``cancelled`` and ``total``, because a cancelled job is neither queued nor
        failed and folding it into either would misreport what happened.
        ``queued`` includes ``validating``, ``processing`` includes
        ``submitting`` and ``downloading``, and ``failed`` includes
        ``timed_out``; ``total`` is every row, so the buckets are checkable.
        """
        scope = _require_workspace(workspace_id)
        rows = rows_to_dicts(
            self._db.query(
                "SELECT status, COUNT(*) AS n FROM generation_jobs"
                " WHERE workspace_id = ? GROUP BY status",
                (scope,),
            )
        )
        by_state = {str(row["status"]): int(row["n"] or 0) for row in rows}
        queued = by_state.get("queued", 0) + by_state.get("validating", 0)
        processing = (
            by_state.get("submitting", 0)
            + by_state.get("processing", 0)
            + by_state.get("downloading", 0)
        )
        completed = by_state.get("completed", 0)
        failed = by_state.get("failed", 0) + by_state.get("timed_out", 0)
        return {
            "queued": queued,
            "processing": processing,
            "completed": completed,
            "failed": failed,
            "cancelled": by_state.get("cancelled", 0),
            "draft": by_state.get("draft", 0),
            "total": sum(by_state.values()),
        }

    def events(self, job_id: str, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The append-only transition log for one job, oldest first.

        Authorises: reading the history of a job. With ``workspace_id`` given --
        which a request-scoped caller must supply -- the job is checked to belong
        to that workspace first, and a foreign job is ``NOT_FOUND``.

        Refuses: a job id alone from an HTTP-facing caller. Without
        ``workspace_id`` this reads across tenants, which is what the worker needs
        when it picks up a job by id and wants to explain what already happened.
        """
        if workspace_id is not None:
            scope = _require_workspace(workspace_id)
            if not self._db.exists(
                "generation_jobs", "id = ? AND workspace_id = ?", (job_id, scope)
            ):
                raise _not_found("job")
        rows = self._db.query(
            "SELECT id, job_id, status, message, detail_json, created_at"
            " FROM job_events WHERE job_id = ? ORDER BY created_at ASC, rowid ASC",
            (job_id,),
        )
        return [
            _shape_row("job_events", row)  # type: ignore[misc]
            for row in rows_to_dicts(rows)
        ]

    def outputs(self, job_id: str, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Everything one job produced, in batch order.

        Authorises: reading output rows of a job. With ``workspace_id``, the job
        is scope-checked first and a foreign job is ``NOT_FOUND``.

        Refuses: an unscoped read from an HTTP-facing caller. An output row whose
        ingest failed is still returned -- with ``asset_id`` NULL and
        ``ingest_error`` set -- because "the provider gave us a link we could not
        fetch" is information the user needs, not an absence to hide.
        """
        if workspace_id is not None:
            scope = _require_workspace(workspace_id)
            if not self._db.exists(
                "generation_jobs", "id = ? AND workspace_id = ?", (job_id, scope)
            ):
                raise _not_found("job")
        rows = self._db.query(
            "SELECT id, job_id, workspace_id, asset_id, batch_index, provider_url,"
            " ingest_error, created_at FROM generation_outputs"
            " WHERE job_id = ? ORDER BY batch_index ASC",
            (job_id,),
        )
        return rows_to_dicts(rows)

    def retryable(self, job_id: str) -> bool:
        """Whether the worker may run this job again.

        Authorises: reading the attempt counter and state of any job, by id alone.
        It deliberately takes no `workspace_id`: this is a worker call, the worker
        sweeps every workspace by design, and the id it passes already came from a
        row the worker claimed.

        Refuses: to say yes for a job that succeeded, was cancelled, has used up
        its attempts, or does not exist. Only ``failed`` and ``timed_out`` are
        retryable, and only while ``attempt < max_attempts``; everything else is
        ``False``, which is why the caller cannot accidentally re-bill a job that
        already produced its outputs.
        """
        row = self._db.query_dict(
            "SELECT status, attempt, max_attempts FROM generation_jobs WHERE id = ?",
            (job_id,),
        )
        if row is None:
            return False
        status = str(row["status"])
        attempt = int(row["attempt"] or 0)
        max_attempts = int(row["max_attempts"] or 1)
        return status in ("failed", "timed_out") and attempt < max_attempts

    def stale_jobs(self, now: str, grace_seconds: int = 0) -> List[Dict[str, Any]]:
        """Jobs whose lease expired more than ``grace_seconds`` ago.

        Authorises: reading, across every workspace, the jobs that are still marked
        in-flight but whose worker stopped renewing -- the exact set a recovery
        sweep must look at. It deliberately takes no `workspace_id` for that
        reason, and returns the rows a sweep needs rather than anything a request
        handler could page through and use to enumerate other workspaces.

        Refuses: to report a job that never held a lease (a queued job is not
        stale, it is waiting), one that already finished, or one whose lease
        expired less than ``grace_seconds`` ago -- that grace window is how a slow
        but healthy worker avoids being declared dead mid-call. ``now`` is required
        so a sweep can run against a fixed instant and be reproducible.
        """
        stamp = _iso(now)
        if stamp is None:
            raise ApiError(VALIDATION_FAILED, "now is required to find stale jobs.", field="now")
        grace = int(clamp(int(grace_seconds or 0), 0, 86400))
        cutoff = _shift_iso(stamp, -grace)
        rows = self._db.query(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
            " WHERE j.lease_expires_at IS NOT NULL AND j.lease_expires_at < ?"
            " AND j.status IN ('submitting', 'processing', 'downloading')"
            " ORDER BY j.lease_expires_at ASC",
            (cutoff,),
        )
        return [_shape_row(self.TABLE, row) for row in rows_to_dicts(rows)]  # type: ignore[misc]

    # -- writes -------------------------------------------------------------
    def create(self, workspace_id: str, user_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Enqueue a generation job.

        Authorises: inserting a job attributed to ``user_id``, provided every id it
        names -- project, connection, model -- is a live row of this workspace, and
        provided the model can actually perform the requested mode.

        Refuses: an unknown field; a modality other than image or video; a mode
        outside that modality's vocabulary (``CAPABILITY_UNSUPPORTED``, naming the
        mode); a project, connection or model from another workspace
        (``NOT_FOUND``, which also covers "does not exist"); an empty prompt; and a
        duplicate ``idempotency_key`` for this workspace (``CONFLICT``) -- the
        unique index is the duplicate-purchase guard, so a double-submit gets a
        conflict rather than a second bill. A job is created ``queued``
        (``draft`` if explicitly asked for) and this layer never creates one in a
        state that implies work already happened.
        """
        scope = _require_workspace(workspace_id)
        values, row_id, echoed_owner = _absorb_row(
            _mapping(payload, "job"), _JOB_ALIASES, "job", scope=scope
        )
        # The explicit argument wins over the payload, so a request body cannot
        # attribute a job to somebody else.
        explicit_owner = _text(user_id)
        owner = explicit_owner or echoed_owner
        if not owner:
            raise ApiError(
                VALIDATION_FAILED,
                "A creator is required to queue a job.",
                field="created_by_user_id",
            )
        _reject_unknown(values, _JOB_FIELDS, "job")

        modality = _choice(values.get("modality"), JOB_MODALITIES, "modality")
        mode = _require_text(values.get("mode"), "mode")
        _check_modes(modality, [mode])
        prompt = _require_text(values.get("prompt"), "prompt", limit=20000)

        project_id = _text(values.get("project_id"))
        if project_id is not None and not self._db.exists(
            "projects", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (project_id, scope)
        ):
            raise _not_found("project")
        connection_id = _text(values.get("connection_id"))
        if connection_id is not None and not self._db.exists(
            "api_connections",
            "id = ? AND workspace_id = ? AND deleted_at IS NULL",
            (connection_id, scope),
        ):
            raise _not_found("connection")
        model_id = _text(values.get("model_id"))
        if model_id is not None:
            model_row = self._db.query_dict(
                "SELECT id, modality, modes_json FROM models WHERE id = ? AND workspace_id = ?",
                (model_id, scope),
            )
            if model_row is None:
                raise _not_found("model")
            # The requested mode must exist on the model, not merely in the
            # modality vocabulary: asking an image model for a mode it never
            # declared is the failure this whole capability model exists to stop.
            # A model that declares no modes therefore accepts none -- "we did not
            # record a capability" is not evidence that the capability exists.
            declared = _json(model_row.get("modes_json"), default=[])
            declared_modes = declared if isinstance(declared, list) else []
            if mode not in declared_modes:
                raise ApiError(
                    CAPABILITY_UNSUPPORTED,
                    "The selected model does not support mode '%s'." % mode,
                    field="mode",
                    details={"mode": mode, "supported_modes": declared_modes},
                    hint="Choose a mode the model declares, or register the mode on the model first.",
                )

        max_attempts = _optional_int(values.get("max_attempts")) or 3
        if max_attempts < 1:
            raise ApiError(
                VALIDATION_FAILED, "max_attempts must be at least 1.", field="max_attempts"
            )
        status = _choice(
            values.get("status"), ("queued", "draft"), "status", default="queued"
        )
        stamp = now_iso()
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "project_id": project_id,
            "connection_id": connection_id,
            "model_id": model_id,
            "modality": modality,
            "mode": mode,
            "prompt": prompt,
            "negative_prompt": truncate(str(values.get("negative_prompt") or ""), 20000) or "",
            "parameters_json": json_dumps(_json_payload(values.get("parameters"), "parameters")),
            "request_json": json_dumps(_json_payload(values.get("request"), "request")),
            "references_json": json_dumps(_reference_list(values.get("references"))),
            "status": status,
            "status_message": "Queued.",
            "attempt": 0,
            "max_attempts": max_attempts,
            "cost_estimate_json": json_dumps(
                _json_payload(values.get("cost_estimate"), "cost_estimate")
            ),
            "idempotency_key": _text(values.get("idempotency_key")),
            "created_at": stamp,
            "updated_at": stamp,
        }
        try:
            with self._db.write_tx() as tx:
                inserted = tx.insert(self.TABLE, record)
                tx.insert(
                    "job_events",
                    {
                        "id": new_id(),
                        "job_id": inserted["id"],
                        "status": status,
                        "message": "Job created.",
                        "detail_json": "{}",
                        "created_at": stamp,
                    },
                )
        except sqlite3.IntegrityError as error:
            message = str(error).lower()
            if "idempotency" in message or "unique" in message:
                raise ApiError(
                    CONFLICT,
                    "That request has already been submitted.",
                    field="idempotency_key",
                    hint="Reuse the existing job instead of submitting it twice.",
                ) from error
            raise _conflict_for(error, "job") from error
        return self.get(scope, inserted["id"])

    def claim_next(
        self, worker_id: str, *, lease_seconds: int, now: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Take the oldest runnable job for this worker, or ``None``.

        Authorises: any worker to claim any tenant's job -- this is the queue, and
        it is deliberately not workspace-scoped. The claim is a single conditional
        UPDATE whose target is chosen by a subquery, inside one BEGIN IMMEDIATE
        transaction.

        Refuses: to hand the same job to two workers, and to claim a job that is
        not due (``next_attempt_at`` still in the future), whose lease has not
        expired, or whose state is not ``queued``/``validating``. It refuses a
        ``lease_seconds`` that would not outlive the call (it must be positive).

        Why the claim is safe under concurrency
        ---------------------------------------
        ``BEGIN IMMEDIATE`` takes SQLite's write lock before the statement runs, so
        two workers calling this at the same instant are serialised: the second one
        does not merely read a stale snapshot, it blocks until the first has
        committed. When it then evaluates its own subquery, the row the first
        worker claimed is already ``submitting`` and therefore no longer matches
        ``status IN ('queued','validating')``, so the subquery picks the *next* row
        or returns nothing. The UPDATE and the choice of row are one statement, so
        there is no window between "I read the id" and "I updated that id" in which
        another worker could take it. That is the whole mechanism: the write lock
        provides mutual exclusion, and the WHERE clause provides re-checking under
        the lock. The lease is the second line of defence, for a worker that dies
        without releasing: ``stale_jobs`` finds the row once
        ``lease_expires_at`` has passed, and the same conditional update then
        makes it claimable again.
        """
        owner = _require_text(worker_id, "worker_id")
        seconds = int(clamp(int(lease_seconds), 0, 86400))
        if seconds <= 0:
            raise ApiError(
                VALIDATION_FAILED,
                "A lease must last at least one second.",
                field="lease_seconds",
            )
        stamp = _iso(now) or now_iso()
        expires = _shift_iso(stamp, seconds)
        with self._db.write_tx() as tx:
            cursor = tx.execute(
                # A queued job is being sent to the provider. A job already in
                # flight keeps the state it reached: claiming it again means
                # "poll it", and rewriting processing as submitting would move a
                # slow job backwards on every pass and lose the fact that the
                # request had already gone out.
                "UPDATE generation_jobs SET"
                " status = CASE WHEN status IN ('queued', 'validating')"
                "   THEN 'submitting' ELSE status END,"
                " lease_owner = ?,"
                " lease_expires_at = ?,"
                " next_attempt_at = NULL,"
                # An attempt is one send to the provider. Polling an in-flight job
                # is not another attempt, and counting it as one would exhaust the
                # retry budget before the job had ever failed.
                " attempt = CASE WHEN status IN ('queued', 'validating')"
                "   THEN attempt + 1 ELSE attempt END,"
                " started_at = COALESCE(started_at, ?),"
                " status_message = CASE WHEN status IN ('queued', 'validating')"
                "   THEN 'Submitting to the provider.' ELSE status_message END,"
                " updated_at = ?"
                " WHERE id = ("
                "   SELECT id FROM generation_jobs"
                "   WHERE (lease_expires_at IS NULL OR lease_expires_at < ?)"
                "     AND ("
                "       ("
                "         status IN ('queued', 'validating')"
                "         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
                "       )"
                "       OR ("
                "         status IN ('processing', 'downloading')"
                "         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
                "       )"
                "     )"
                "   ORDER BY created_at ASC"
                "   LIMIT 1"
                " ) RETURNING id, workspace_id, attempt, status",
                (owner, expires, stamp, stamp, stamp, stamp, stamp),
            )
            claimed = cursor.fetchall()
            if not claimed:
                return None
            job_id = claimed[0]["id"]
            attempt = int(claimed[0]["attempt"] or 0)
            # The event records the state the job is actually in after this claim,
            # which is "submitting" only for a first send.
            claimed_state = str(claimed[0]["status"] or "submitting")
            # The claim is one statement; the log entry that explains it is a
            # second one in the *same* transaction, so the history can never be
            # missing the move the job actually made.
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": job_id,
                    "status": claimed_state,
                    "message": (
                        "Claimed by worker."
                        if claimed_state == "submitting"
                        else "Picked up again to check on the provider."
                    ),
                    "detail_json": json_dumps(
                        {"worker_id": owner, "attempt": attempt, "lease_expires_at": expires}
                    ),
                    "created_at": stamp,
                },
            )
        row = self._db.query_dict(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j WHERE j.id = ?", (job_id,)
        )
        return _shape_row(self.TABLE, row) if row is not None else None

    def transition(
        self,
        workspace_id: str,
        job_id: str,
        *,
        state: str,
        message: str = "",
        detail: Any = None,
        error: Any = None,
        provider_job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move a job to a new state and append the event that records it.

        Authorises: one transaction that updates the job row and inserts exactly
        one ``job_events`` row, for a job in this workspace.

        Refuses: an unknown target state; a transition not in
        :data:`LEGAL_TRANSITIONS` -- completed to processing included -- which
        raises ``CONFLICT`` naming both the current and the refused state; a job
        that is missing or foreign (``NOT_FOUND``). It refuses to write
        ``completed`` while ``output_count`` is zero, because a job is only
        complete when an artifact actually exists, and it refuses ``failed``
        without a diagnostic, because a failure with no reason is not a record of
        anything.
        """
        scope = _require_workspace(workspace_id)
        target = _choice(state, JOB_STATES, "state")
        current = self._scoped_row(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (job_id, scope),
            "job",
        )
        self._check_transition(current, target)
        self._check_payload(current, target, error)
        detail_payload = _json_payload(detail, "detail") if detail is not None else {}
        updates = self._transition_updates(current, target, message, error, provider_job_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                updates,
                "id = ? AND workspace_id = ?",
                (job_id, scope),
            )
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": job_id,
                    "status": target,
                    "message": truncate(str(message or ""), 2000) or "",
                    "detail_json": json_dumps(detail_payload),
                    "created_at": updates.get("updated_at") or now_iso(),
                },
            )
        return self.get(scope, job_id)

    def request_cancel(self, workspace_id: str, job_id: str) -> Dict[str, Any]:
        """Ask for a job to stop.

        Authorises: for a job that has not reached a provider yet
        (``draft``/``queued``/`validating``), moving it straight to ``cancelled`
        -- nothing was submitted, so nothing can be wasted. For a job already in
        flight, setting ``cancel_requested_at`` and leaving the state alone until
        the worker reaches a checkpoint.

        Refuses: a job that is already terminal (``CONFLICT`, naming the state),
        because "cancel" a completed job describes an action nobody can take; a
        job that is missing or foreign (``NOT_FOUND``). It does not claim the
        provider stopped anything: only the worker can observe that, and it
        records it through :meth:`transition`.
        """
        scope = _require_workspace(workspace_id)
        current = self._scoped_row(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (job_id, scope),
            "job",
        )
        status = str(current["status"])
        if status in TERMINAL_STATES:
            raise ApiError(
                CONFLICT,
                "This job is already '%s' and cannot be cancelled." % status,
                details={"job_id": job_id, "state": status},
            )
        if status in ("draft", "queued", "validating"):
            return self.transition(
                scope,
                job_id,
                state="cancelled",
                message="Cancelled before it was submitted to the provider.",
            )
        stamp = now_iso()
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"cancel_requested_at": stamp, "status_message": "Cancellation requested.", "updated_at": stamp},
                "id = ? AND workspace_id = ?",
                (job_id, scope),
            )
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": job_id,
                    "status": status,
                    "message": "Cancellation requested; waiting for the worker to stop.",
                    "detail_json": "{}",
                    "created_at": stamp,
                },
            )
        return self.get(scope, job_id)

    def release_lease(
        self,
        job_id: str,
        *,
        next_attempt_at: Optional[str] = None,
        state: Optional[str] = None,
        message: str = "",
        error: Any = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Give up, or hand back, the lease on a job.

        Authorises: the worker that holds a job -- or the recovery sweep acting on
        it -- to clear ``lease_owner``/``lease_expires_at`, optionally move the
        job to a new state through the same legality check as :meth:`transition`,
        and optionally schedule the next attempt.

        Refuses: an unknown target state; an illegal transition (``CONFLICT`
        naming both states); a ``next_attempt_at`` that is not a timestamp; and,
        when ``workspace_id`` is supplied, a job belonging to a different
        workspace (``NOT_FOUND``). Without ``workspace_id`` this works by job id
        alone -- it is a worker call, and the worker is entitled to see every
        tenant.
        """
        if workspace_id is not None:
            scope = _require_workspace(workspace_id)
            current = self._scoped_row(
                "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j"
                " WHERE j.id = ? AND j.workspace_id = ?",
                (job_id, scope),
                "job",
            )
        else:
            row = self._db.query_dict(
                "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j WHERE j.id = ?", (job_id,)
            )
            if row is None:
                raise _not_found("job")
            current = _shape_row(self.TABLE, row) or {}

        stamp = now_iso()
        updates: Dict[str, Any] = {
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": stamp,
        }
        target = None
        if state is not None:
            target = _choice(state, JOB_STATES, "state")
            self._check_transition(current, target)
            self._check_payload(current, target, error)
            updates.update(self._transition_updates(current, target, message, error, None))
        elif message:
            updates["status_message"] = truncate(str(message), 2000)
        if next_attempt_at is not None:
            updates["next_attempt_at"] = _iso(next_attempt_at) or now_iso()

        with self._db.write_tx() as tx:
            self._db.update_where(self.TABLE, updates, "id = ?", (job_id,))
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": job_id,
                    "status": target or str(current["status"]),
                    "message": truncate(str(message or ""), 2000)
                    or "Lease released.",
                    "detail_json": json_dumps(
                        {"next_attempt_at": updates.get("next_attempt_at")}
                    ),
                    "created_at": stamp,
                },
            )
        out = self._db.query_dict(
            "SELECT " + self._projection("generation_jobs", "j") + " FROM generation_jobs j WHERE j.id = ?", (job_id,)
        )
        return _shape_row(self.TABLE, out) or {}

    def add_output(
        self,
        job_id: str,
        workspace_id: str,
        batch_index: int,
        *,
        asset_id: Optional[str] = None,
        provider_url: Optional[str] = None,
        ingest_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record one artifact a job produced, and raise the job's output count.

        Authorises: inserting a ``generation_outputs`` row for a job in this
        workspace and bumping ``output_count`` to the real number of rows, in one
        transaction. ``asset_id`` may be given only when that asset belongs to the
        same workspace.

        Refuses: a job in another workspace (``NOT_FOUND``); a negative batch
        index; a second output for the same ``(job_id, batch_index)``
        (``CONFLICT`` -- the schema's unique index exists so a retried download
        cannot double the result set); an asset id from another workspace
        (``NOT_FOUND``); and an output with neither an asset nor a provider URL
        nor an ingest error, which would be a row that records nothing.
        """
        scope = _require_workspace(workspace_id)
        index = int(batch_index)
        if index < 0:
            raise ApiError(
                VALIDATION_FAILED, "batch_index cannot be negative.", field="batch_index"
            )
        if asset_id is None and provider_url is None and ingest_error is None:
            raise ApiError(
                VALIDATION_FAILED,
                "An output must record an asset, a provider URL, or an ingest error.",
            )
        if not self._db.exists(
            "generation_jobs", "id = ? AND workspace_id = ?", (job_id, scope)
        ):
            raise _not_found("job")
        if asset_id is not None and not self._db.exists(
            "assets", "id = ? AND workspace_id = ?", (asset_id, scope)
        ):
            raise _not_found("asset")
        stamp = now_iso()
        record = {
            "id": new_id(),
            "job_id": job_id,
            "workspace_id": scope,
            "asset_id": asset_id,
            "batch_index": index,
            "provider_url": _text(provider_url),
            "ingest_error": truncate(str(ingest_error), 2000) if ingest_error else None,
            "created_at": stamp,
        }
        try:
            with self._db.write_tx() as tx:
                inserted = tx.insert("generation_outputs", record)
                total = int(
                    tx.scalar(
                        "SELECT COUNT(*) FROM generation_outputs WHERE job_id = ?",
                        (job_id,),
                        default=0,
                    )
                    or 0
                )
                self._db.update_where(
                    self.TABLE,
                    {"output_count": total, "updated_at": stamp},
                    "id = ? AND workspace_id = ?",
                    (job_id, scope),
                )
        except sqlite3.IntegrityError as error:
            raise ApiError(
                CONFLICT,
                "That output batch has already been recorded for this job.",
                field="batch_index",
                details={"job_id": job_id, "batch_index": index},
            ) from error
        shaped = _shape_row("generation_outputs", inserted)
        return shaped or {}

    # -- internals ----------------------------------------------------------
    def _check_transition(self, current: Mapping[str, Any], target: str) -> None:
        """Refuse an illegal move, naming both states.

        The message names the current and the refused state so an operator reading
        a log learns the actual sequence, and the details carry the legal set so a
        client can render what *would* have been allowed.
        """
        status = str(current.get("status"))
        allowed = LEGAL_TRANSITIONS.get(status, frozenset())
        if target == status or target not in allowed:
            raise ApiError(
                CONFLICT,
                "Cannot move this job from '%s' to '%s'." % (status, target),
                details={
                    "job_id": current.get("id"),
                    "from": status,
                    "to": target,
                    "allowed": sorted(allowed),
                },
            )

    def _check_payload(self, current: Mapping[str, Any], target: str, error: Any) -> None:
        """Refuse a state the record cannot honestly claim.

        Two rules, both about not writing something untrue: ``completed`` requires
        at least one recorded output, because a job is only finished once an
        artifact exists; ``failed`` requires a diagnostic, because a failure with
        no reason is not a record of anything. Applies to ``transition`` and to
        ``release_lease`` alike, so neither is a way around the other.
        """
        if target == "completed" and not int(current.get("output_count") or 0):
            raise ApiError(
                CONFLICT,
                "A job cannot be completed before an output exists.",
                hint="Record the output with add_output, then complete the job.",
                details={
                    "job_id": current.get("id"),
                    "output_count": int(current.get("output_count") or 0),
                },
            )
        if target == "failed" and error is None:
            raise ApiError(
                VALIDATION_FAILED,
                "A job cannot be marked failed without a diagnostic.",
                field="error",
            )

    def _transition_updates(
        self,
        current: Mapping[str, Any],
        target: str,
        message: str,
        error: Any,
        provider_job_id: Optional[str],
    ) -> Dict[str, Any]:
        """The column values a move to ``target`` implies.

        Timestamps are written from what the move means, not from a generic
        "touch": ``submitted_at`` only when the job first goes out,
        ``started_at`` only when it first starts, ``finished_at`` only when it
        stops. Repeating a move cannot therefore rewrite when it happened.
        """
        stamp = now_iso()
        updates: Dict[str, Any] = {
            "status": target,
            "status_message": truncate(str(message or ""), 2000)
            or _default_message(target),
            "updated_at": stamp,
        }
        if provider_job_id is not None:
            updates["provider_job_ref"] = _text(provider_job_id)
        if target == "submitting" and not current.get("submitted_at"):
            updates["submitted_at"] = stamp
        if target == "processing" and not current.get("started_at"):
            updates["started_at"] = stamp
        if target in TERMINAL_STATES:
            updates["finished_at"] = stamp
            updates["next_attempt_at"] = None
        if target == "queued":
            # A retry re-enters the queue; it must not look finished any more.
            updates["finished_at"] = None
        updates.update(_error_columns(error))
        return updates

    # -- lineage and idempotency, used by the generation service -------------

    def find_by_idempotency(
        self, workspace_id: str, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """The job a repeated submission already created, or ``None``.

        Authorises: reading one job by the key its submitter supplied, scoped to
        this workspace. ``generation_jobs`` has a unique index on
        ``(workspace_id, idempotency_key)``, so this is a lookup of at most one row
        rather than a search.

        Refuses: another workspace's job. An absent key or an unknown key returns
        ``None`` instead of raising, because "this has not been submitted yet" is
        the ordinary answer on the request path and not an error.
        """
        scope = _require_workspace(workspace_id)
        key = _text(idempotency_key)
        if not key:
            return None
        row = self._db.query_dict(
            "SELECT " + self._projection(self.TABLE, "j") + " FROM generation_jobs j"
            " WHERE j.workspace_id = ? AND j.idempotency_key = ?",
            (scope, key),
        )
        if row is None:
            return None
        return self.get(scope, str(row["id"]))

    def link_parent(
        self, workspace_id: str, job_id: str, parent_job_id: str, relation: str
    ) -> Dict[str, Any]:
        """Record that a job descends from another one, and how.

        Authorises: setting ``parent_job_id`` and ``relation`` on a job in this
        workspace, provided the parent is a job of the same workspace. A variation,
        an edit, an extension and a transform are all new jobs; the job they came
        from is never modified, so its output and its request stay exactly as they
        were.

        Refuses: a job or a parent that is missing or foreign (``NOT_FOUND``);
        a job naming itself as its own parent (``CONFLICT``); a job that already
        has a parent (``CONFLICT`` -- one edge per job, so lineage is a tree and
        not a graph with surprising loops); and an empty relation, because an edge
        with no name does not say how the two are related. Appends one
        ``job_events`` row in the same transaction, so the history records where
        the job came from instead of leaving it to be inferred.
        """
        scope = _require_workspace(workspace_id)
        child = _require_text(job_id, "job_id")
        parent = _require_text(parent_job_id, "parent_job_id")
        label = _require_text(relation, "relation", limit=40)
        if child == parent:
            raise ApiError(
                CONFLICT,
                "A job cannot be derived from itself.",
                field="parent_job_id",
                details={"job_id": child},
            )
        current = self._scoped_row(
            "SELECT " + self._projection(self.TABLE, "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (child, scope),
            "job",
        )
        if current.get("parent_job_id"):
            raise ApiError(
                CONFLICT,
                "This job already descends from another job.",
                details={"job_id": child, "parent_job_id": current.get("parent_job_id")},
            )
        self._scoped_row(
            "SELECT " + self._projection(self.TABLE, "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (parent, scope),
            "parent job",
        )
        stamp = now_iso()
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"parent_job_id": parent, "relation": label, "updated_at": stamp},
                "id = ? AND workspace_id = ?",
                (child, scope),
            )
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": child,
                    "status": str(current["status"]),
                    "message": "Derived from an earlier job (%s)." % label,
                    "detail_json": json_dumps({"parent_job_id": parent, "relation": label}),
                    "created_at": stamp,
                },
            )
        return self.get(scope, child)

    def annotate(
        self,
        workspace_id: str,
        job_id: str,
        *,
        message: str,
        detail: Any = None,
        provider_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an observation about a job without pretending it moved.

        Authorises: appending one ``job_events`` row to a job in this workspace and
        updating its status message, so a poll that reported "still running" is
        visible in the history with a timestamp.

        Refuses: an empty message (an event that says nothing is noise); a job that
        is missing or foreign (``NOT_FOUND``). It deliberately does not consult
        :data:`LEGAL_TRANSITIONS`: nothing is moving. Recorded as a transition, a
        "still processing" poll would either be refused for repeating the state or,
        worse, claim a move that never happened.
        """
        scope = _require_workspace(workspace_id)
        note = _require_text(message, "message", limit=2000)
        current = self._scoped_row(
            "SELECT " + self._projection(self.TABLE, "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (job_id, scope),
            "job",
        )
        payload = dict(_json_payload(detail, "detail") if detail is not None else {})
        if provider_status is not None:
            payload.setdefault("provider_status", truncate(str(provider_status), 200))
        # Marked as an observation, and stamped with the state the job was in when
        # it was made. The state is recorded because a log you can filter by state
        # is worth having; the flag is recorded so nobody reads two rows with the
        # same status as two transitions.
        payload["observed"] = True
        stamp = now_iso()
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"status_message": truncate(note, 2000), "updated_at": stamp},
                "id = ? AND workspace_id = ?",
                (job_id, scope),
            )
            tx.insert(
                "job_events",
                {
                    "id": new_id(),
                    "job_id": job_id,
                    "status": str(current["status"]),
                    "message": truncate(note, 2000),
                    "detail_json": json_dumps(payload),
                    "created_at": stamp,
                },
            )
        return self.get(scope, job_id)

    def update_routes(self, workspace_id: str, job_id: str, request_json: Any) -> Dict[str, Any]:
        """Replace a job's stored request snapshot.

        Authorises: overwriting ``request_json`` on a job in this workspace. The
        snapshot is what makes a job reproducible after a model's schema changed, so
        a provider route discovered at submit time belongs in it.

        Refuses: a snapshot that is not valid JSON, or whose top-level value is not
        an object -- storing unparseable text there would break every later read of
        the job; and a job that is missing or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        parsed = _json_payload(request_json, "request_json")
        if not isinstance(parsed, dict):
            raise ApiError(
                VALIDATION_FAILED,
                "The request snapshot must be a JSON object.",
                field="request_json",
            )
        self._scoped_row(
            "SELECT " + self._projection(self.TABLE, "j") + " FROM generation_jobs j"
            " WHERE j.id = ? AND j.workspace_id = ?",
            (job_id, scope),
            "job",
        )
        with self._db.write_tx() as tx:
            self._db.update_where(
                self.TABLE,
                {"request_json": json_dumps(parsed), "updated_at": now_iso()},
                "id = ? AND workspace_id = ?",
                (job_id, scope),
            )
        return self.get(scope, job_id)


def _default_message(state: str) -> str:
    """A status line for a move the caller did not describe."""
    return {
        "draft": "Draft.",
        "queued": "Queued.",
        "validating": "Validating the request.",
        "submitting": "Submitting to the provider.",
        "processing": "The provider is working on it.",
        "downloading": "Downloading the result.",
        "completed": "Completed.",
        "failed": "Failed.",
        "cancelled": "Cancelled.",
        "timed_out": "Timed out.",
    }.get(state, state.replace("_", " ").capitalize() + ".")


def _shift_iso(stamp: str, seconds: int) -> str:
    """Move a fixed-width UTC timestamp by ``seconds`, staying in the same format.

    Used for lease expiry. Parsed rather than string-sliced so a leap second or a
    month boundary cannot produce a deadline in the wrong minute.
    """
    parsed = parse_iso(stamp) or parse_iso(now_iso())
    if parsed is None:  # pragma: no cover - now_iso() always parses
        raise ApiError(VALIDATION_FAILED, "A timestamp could not be read.", field="now")
    return iso(parsed + timedelta(seconds=seconds))


def _reference_list(value: Any) -> List[Any]:
    """Normalise a job's reference payload to a JSON-serialisable list."""
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json_loads(value, default=None)
        if parsed is None:
            raise ApiError(
                VALIDATION_FAILED, "references must be a list.", field="references"
            )
        value = parsed
    if not isinstance(value, (list, tuple)):
        raise ApiError(VALIDATION_FAILED, "references must be a list.", field="references")
    out: List[Any] = []
    for item in value:
        if isinstance(item, Mapping):
            entry: Dict[str, Any] = {}
            asset_id = _text(item.get("asset_id"))
            if not asset_id:
                raise ApiError(
                    VALIDATION_FAILED,
                    "Every reference must name an asset_id.",
                    field="references",
                )
            entry["asset_id"] = asset_id
            role = _text(item.get("role"))
            if role:
                entry["role"] = role
            if "weight" in item and item["weight"] is not None:
                entry["weight"] = _optional_float(item["weight"])
            note = _text(item.get("notes") or item.get("note"))
            if note:
                entry["notes"] = truncate(note, 500)
            out.append(entry)
        elif _text(item):
            out.append({"asset_id": _text(item)})
        else:
            raise ApiError(
                VALIDATION_FAILED,
                "Every reference must be an object with an asset_id or an asset id.",
                field="references",
            )
    return out


# ---------------------------------------------------------------------------
# Library: presets, saved prompts, characters
# ---------------------------------------------------------------------------

#: Payload keys accepted by :meth:`LibraryRepository.create_preset`.
_PRESET_FIELDS: Tuple[str, ...] = (
    "slug",
    "kind",
    "name",
    "description",
    "category",
    "thumbnail_url",
    "prompt_template",
    "negative_prompt_template",
    "defaults",
    "supported_modes",
    "required_references",
    "camera_settings",
    "tags",
)

#: Payload keys accepted by :meth:`LibraryRepository.create_prompt`.
_PROMPT_FIELDS: Tuple[str, ...] = (
    "title",
    "body",
    "negative_body",
    "variables",
    "tags",
    "modality",
)

#: Payload keys accepted by :meth:`LibraryRepository.create_character`.
_CHARACTER_FIELDS: Tuple[str, ...] = (
    "name",
    "description",
    "attributes",
    "prompt_details",
    "preferred_models",
    "tags",
    "parent_character_id",
)


class LibraryRepository(_Repository):
    """Reusable creative material: presets, saved prompts, and characters.

    Presets are the one aggregate with a shared tier: a row with a NULL
    ``workspace_id`` is a platform built-in visible to everybody. Everything else
    is strictly per-workspace. Built-ins are readable here and refused as write
    targets, because a workspace does not own them.
    """

    TABLE = "presets"

    # -- presets ------------------------------------------------------------
    def list_presets(
        self,
        workspace_id: str,
        *,
        kind: Optional[str] = None,
        category: Optional[str] = None,
        include_builtin: bool = True,
        favorite: Optional[bool] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Presets visible to one workspace: its own, plus the built-ins.

        Authorises: reading this workspace's presets and, deliberately, the
        platform built-ins, which belong to no workspace and are marked
        ``is_builtin`` in every row that carries one.

        Refuses: another workspace's presets, always. Refuses a ``favorite``
        filter when no ``user_id`` is supplied, because favourites are per person
        (``preset_favorites`` is keyed by user) and filtering by somebody else's
        would be meaningless; that case raises a validation error rather than
        returning a wrong list.
        """
        scope = _require_workspace(workspace_id)
        clauses: List[str] = []
        params: List[Any] = []
        if include_builtin:
            clauses.append("(p.workspace_id = ? OR p.workspace_id IS NULL)")
            params.append(scope)
        else:
            clauses.append("p.workspace_id = ?")
            params.append(scope)
        if kind is not None:
            clauses.append("p.kind = ?")
            params.append(_choice(kind, PRESET_KINDS, "kind"))
        if category is not None:
            clauses.append("p.category = ?")
            params.append(_require_text(category, "category"))
        if favorite is not None:
            user = _require_text(user_id, "user_id")
            clauses.append(
                "EXISTS (SELECT 1 FROM preset_favorites f WHERE f.preset_id = p.id"
                " AND f.user_id = ?)"
                if _flag(favorite)
                else "NOT EXISTS (SELECT 1 FROM preset_favorites f WHERE f.preset_id = p.id"
                " AND f.user_id = ?)"
            )
            params.append(user)
        sql = (
            "SELECT p.id, p.workspace_id, p.created_by_user_id, p.slug, p.kind, p.name,"
            " p.description, p.category, p.thumbnail_url, p.prompt_template,"
            " p.negative_prompt_template, p.defaults_json, p.supported_modes_json,"
            " p.required_references_json, p.camera_settings_json, p.tags_json,"
            " p.is_builtin, p.created_at, p.updated_at"
            " FROM presets p WHERE " + " AND ".join(clauses) +
            " ORDER BY p.is_builtin DESC, p.category ASC, p.name COLLATE NOCASE ASC"
        )
        return self._all(sql, params)

    def get_preset(self, workspace_id: str, preset_id: str) -> Dict[str, Any]:
        """One preset, if this workspace may see it.

        Authorises: reading a preset the workspace owns or a platform built-in.

        Refuses: a preset belonging to another workspace, reported exactly as a
        preset that does not exist (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        return self._scoped_row(
            "SELECT * FROM presets p WHERE p.id = ?"
            " AND (p.workspace_id = ? OR p.workspace_id IS NULL)",
            (preset_id, scope),
            "preset",
        )

    def create_preset(self, workspace_id: str, user_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Add a preset to this workspace's library.

        Authorises: inserting a workspace-owned preset, with a slug derived from
        the name when the caller does not supply one and made unique among this
        workspace's own slugs with :func:`studio.util.unique_slug`.

        Refuses: an unknown field; a kind outside image/video; a supported mode
        outside that kind's vocabulary (``CAPABILITY_UNSUPPORTED``, naming the
        mode) -- a preset may not name a capability the platform cannot request;
        a built-in flag (only the deployment seeds built-ins); and a slug already
        used in this workspace (``CONFLICT``).
        """
        scope = _require_workspace(workspace_id)
        owner = _require_text(user_id, "user_id")
        values, row_id, echoed_owner = _absorb_row(
            _mapping(payload, "preset"), _LIBRARY_ALIASES, "preset", scope=scope
        )
        owner = echoed_owner or owner
        _reject_unknown(values, _PRESET_FIELDS, "preset")
        kind = _choice(values.get("kind"), PRESET_KINDS, "kind")
        name = _require_text(values.get("name"), "name", limit=200)
        modes = _mode_list(values.get("supported_modes"))
        _check_modes(kind, modes)
        slug_source = _text(values.get("slug")) or name
        base = slugify(slug_source, fallback="preset")
        taken = [
            row["slug"]
            for row in rows_to_dicts(
                self._db.query("SELECT slug FROM presets WHERE workspace_id = ?", (scope,))
            )
        ]
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "slug": unique_slug(base, taken),
            "kind": kind,
            "name": name,
            "description": truncate(str(values.get("description") or ""), 4000) or "",
            "category": _text(values.get("category")) or "general",
            "thumbnail_url": _text(values.get("thumbnail_url")),
            "prompt_template": truncate(str(values.get("prompt_template") or ""), 20000) or "",
            "negative_prompt_template": truncate(
                str(values.get("negative_prompt_template") or ""), 20000
            )
            or "",
            "defaults_json": json_dumps(_json_payload(values.get("defaults"), "defaults")),
            "supported_modes_json": json_dumps(modes),
            "required_references_json": json_dumps(
                _json_payload(values.get("required_references"), "required_references")
            ),
            "camera_settings_json": json_dumps(
                _json_payload(values.get("camera_settings"), "camera_settings")
            ),
            "tags_json": json_dumps(_tag_list(values.get("tags"))),
            "is_builtin": 0,
        }
        try:
            with self._db.write_tx() as tx:
                inserted = tx.insert("presets", record)
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "preset slug") from error
        return self.get_preset(scope, inserted["id"])

    def update_preset(
        self, workspace_id: str, preset_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a preset this workspace owns.

        Authorises: updating the mutable fields of a workspace-owned preset.

        Refuses: every built-in (``FORBIDDEN``: it is not this workspace's row to
        edit), a preset from another workspace (``NOT_FOUND``), an unknown field,
        ``is_builtin``/``workspace_id``, and a supported mode that does not
        belong to the preset's kind (``CAPABILITY_UNSUPPORTED``).
        """
        scope = _require_workspace(workspace_id)
        current = self.get_preset(scope, preset_id)
        if current.get("workspace_id") != scope:
            raise ApiError(
                FORBIDDEN,
                "A platform built-in preset cannot be edited; duplicate it into the workspace.",
            )
        values, _row, _owner = _absorb_row(
            _mapping(fields, "preset"), _LIBRARY_ALIASES, "preset", scope=scope
        )
        _reject_unknown(values, _PRESET_FIELDS, "preset")
        updates: Dict[str, Any] = {}
        kind = current["kind"]
        if "kind" in values:
            kind = _choice(values.get("kind"), PRESET_KINDS, "kind")
            updates["kind"] = kind
        if "supported_modes" in values:
            modes = _mode_list(values.get("supported_modes"))
            _check_modes(kind, modes)
            updates["supported_modes_json"] = json_dumps(modes)
        elif "kind" in values:
            # The kind moved, so the modes that were valid before must be
            # re-checked against the new vocabulary: a preset left claiming a mode
            # its kind cannot perform is a button that fails after the user has
            # typed everything.
            _check_modes(kind, list(current.get("supported_modes_json") or []))
        if "name" in values:
            updates["name"] = _require_text(values.get("name"), "name", limit=200)
        if "description" in values:
            updates["description"] = truncate(str(values.get("description") or ""), 4000) or ""
        if "category" in values:
            updates["category"] = _text(values.get("category")) or "general"
        if "thumbnail_url" in values:
            updates["thumbnail_url"] = _text(values.get("thumbnail_url"))
        if "prompt_template" in values:
            updates["prompt_template"] = (
                truncate(str(values.get("prompt_template") or ""), 20000) or ""
            )
        if "negative_prompt_template" in values:
            updates["negative_prompt_template"] = (
                truncate(str(values.get("negative_prompt_template") or ""), 20000) or ""
            )
        if "defaults" in values:
            updates["defaults_json"] = json_dumps(_json_payload(values.get("defaults"), "defaults"))
        if "required_references" in values:
            updates["required_references_json"] = json_dumps(
                _json_payload(values.get("required_references"), "required_references")
            )
        if "camera_settings" in values:
            updates["camera_settings_json"] = json_dumps(
                _json_payload(values.get("camera_settings"), "camera_settings")
            )
        if "tags" in values:
            updates["tags_json"] = json_dumps(_tag_list(values.get("tags")))
        if not updates:
            return self.get_preset(scope, preset_id)
        try:
            with self._db.write_tx() as tx:
                self._db.update_where(
                    "presets", updates, "id = ? AND workspace_id = ?", (preset_id, scope)
                )
        except sqlite3.IntegrityError as error:
            raise _conflict_for(error, "preset slug") from error
        return self.get_preset(scope, preset_id)

    def soft_delete_preset(self, workspace_id: str, preset_id: str) -> None:
        """Remove a preset from this workspace's library.

        Authorises: deleting a workspace-owned preset row outright; ``presets``
        has no ``deleted_at``, and a preset holds no history worth keeping once
        the workspace says it is gone.

        Refuses: a built-in (``FORBIDDEN``) and a preset from another workspace
        (``NOT_FOUND``). It does not touch ``preset_favorites`` directly; the
        schema's cascade does that.
        """
        scope = _require_workspace(workspace_id)
        current = self.get_preset(scope, preset_id)
        if current.get("workspace_id") != scope:
            raise ApiError(
                FORBIDDEN, "A platform built-in preset cannot be deleted."
            )
        with self._db.write_tx() as tx:
            tx.execute("DELETE FROM presets WHERE id = ? AND workspace_id = ?", (preset_id, scope))

    def set_preset_favorite(
        self, workspace_id: str, user_id: str, preset_id: str, favorite: bool = True
    ) -> Dict[str, Any]:
        """Favourite or unfavourite a preset for one person.

        Authorises: inserting or deleting a ``preset_favorites`` row for
        ``user_id``, provided the preset is visible to this workspace.

        Refuses: a preset this workspace cannot see (``NOT_FOUND``), and an empty
        user id. Favourites are per user, so one member favouriting a shared preset
        never changes what another member sees.
        """
        scope = _require_workspace(workspace_id)
        user = _require_text(user_id, "user_id")
        self.get_preset(scope, preset_id)
        with self._db.write_tx() as tx:
            if favorite:
                tx.execute(
                    "INSERT OR IGNORE INTO preset_favorites (user_id, preset_id, created_at)"
                    " VALUES (?, ?, ?)",
                    (user, preset_id, now_iso()),
                )
            else:
                tx.execute(
                    "DELETE FROM preset_favorites WHERE user_id = ? AND preset_id = ?",
                    (user, preset_id),
                )
        return {"preset_id": preset_id, "user_id": user, "favorite": bool(favorite)}

    # -- saved prompts ------------------------------------------------------
    def list_prompts(
        self,
        workspace_id: str,
        *,
        modality: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Saved prompts in one workspace, most recently changed first.

        Authorises: reading this workspace's prompt library.

        Refuses: another workspace's prompts; an unknown modality; and more than
        200 rows in one page (clamped).
        """
        scope = _require_workspace(workspace_id)
        clauses = ["workspace_id = ?"]
        params: List[Any] = [scope]
        if modality is not None:
            clauses.append("modality = ?")
            params.append(_choice(modality, JOB_MODALITIES, "modality"))
        if q is not None and _text(q):
            pattern = _like(_text(q) or "")
            clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        params.append(int(clamp(int(limit), 1, 200)))
        sql = (
            "SELECT * FROM saved_prompts WHERE " + " AND ".join(clauses) +
            " ORDER BY updated_at DESC LIMIT ?"
        )
        return self._all(sql, params, table="saved_prompts")

    def get_prompt(self, workspace_id: str, prompt_id: str) -> Dict[str, Any]:
        """One saved prompt, inside one workspace.

        Authorises: reading a prompt this workspace owns.

        Refuses: a prompt that is missing or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        return self._scoped_row(
            "SELECT * FROM saved_prompts WHERE id = ? AND workspace_id = ?",
            (prompt_id, scope),
            "saved prompt",
            table="saved_prompts",
        )

    def create_prompt(self, workspace_id: str, user_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Save a prompt to this workspace's library.

        Authorises: inserting a prompt attributed to ``user_id`, with the body and
        the template variable declarations as given.

        Refuses: an unknown field; a missing title or body; a modality outside
        image/video; and an empty workspace or user id.
        """
        scope = _require_workspace(workspace_id)
        owner = _require_text(user_id, "user_id")
        values, row_id, echoed_owner = _absorb_row(
            _mapping(payload, "prompt"), _LIBRARY_ALIASES, "prompt", scope=scope
        )
        owner = echoed_owner or owner
        _reject_unknown(values, _PROMPT_FIELDS, "prompt")
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "title": _require_text(values.get("title"), "title", limit=200),
            "body": _require_text(values.get("body"), "body", limit=20000),
            "negative_body": truncate(str(values.get("negative_body") or ""), 20000) or "",
            "variables_json": json_dumps(
                _json_payload(values.get("variables"), "variables")
            ),
            "tags_json": json_dumps(_tag_list(values.get("tags"))),
            "modality": (
                None
                if values.get("modality") is None
                else _choice(values.get("modality"), JOB_MODALITIES, "modality")
            ),
            "use_count": 0,
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert("saved_prompts", record)
        return self.get_prompt(scope, inserted["id"])

    def update_prompt(
        self, workspace_id: str, prompt_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a saved prompt.

        Authorises: updating the title, body, negative body, variables, tags and
        modality of a prompt in this workspace.

        Refuses: an unknown field, ``use_count`` and ``last_used_at`` (those are
        observations, written by :meth:`mark_prompt_used`), and a prompt that is
        missing or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        values, _row, _owner = _absorb_row(
            _mapping(fields, "prompt"), _LIBRARY_ALIASES, "prompt", scope=scope
        )
        _reject_unknown(values, _PROMPT_FIELDS, "prompt")
        self.get_prompt(scope, prompt_id)
        updates: Dict[str, Any] = {}
        if "title" in values:
            updates["title"] = _require_text(values.get("title"), "title", limit=200)
        if "body" in values:
            updates["body"] = _require_text(values.get("body"), "body", limit=20000)
        if "negative_body" in values:
            updates["negative_body"] = truncate(str(values.get("negative_body") or ""), 20000) or ""
        if "variables" in values:
            updates["variables_json"] = json_dumps(_json_payload(values.get("variables"), "variables"))
        if "tags" in values:
            updates["tags_json"] = json_dumps(_tag_list(values.get("tags")))
        if "modality" in values:
            updates["modality"] = (
                None
                if values.get("modality") is None
                else _choice(values.get("modality"), JOB_MODALITIES, "modality")
            )
        if not updates:
            return self.get_prompt(scope, prompt_id)
        with self._db.write_tx() as tx:
            self._db.update_where("saved_prompts", updates, "id = ? AND workspace_id = ?", (prompt_id, scope))
        return self.get_prompt(scope, prompt_id)

    def mark_prompt_used(self, workspace_id: str, prompt_id: str) -> Dict[str, Any]:
        """Record that a saved prompt was actually used in a submission.

        Authorises: incrementing ``use_count`` and stamping ``last_used_at`` on a
        prompt in this workspace.

        Refuses: a prompt that is missing or foreign (``NOT_FOUND``). This is the
        only writer of those two columns, so "used twice" means two submissions
        rather than two page views.
        """
        scope = _require_workspace(workspace_id)
        self.get_prompt(scope, prompt_id)
        with self._db.write_tx() as tx:
            tx.execute(
                "UPDATE saved_prompts SET use_count = use_count + 1, last_used_at = ?,"
                " updated_at = ? WHERE id = ? AND workspace_id = ?",
                (now_iso(), now_iso(), prompt_id, scope),
            )
        return self.get_prompt(scope, prompt_id)

    def soft_delete_prompt(self, workspace_id: str, prompt_id: str) -> None:
        """Remove a saved prompt.

        Authorises: deleting the row, since ``saved_prompts`` has no
        ``deleted_at`` and a prompt is a text snippet with no provenance to keep.

        Refuses: a prompt that is missing or foreign (``NOT_FOUND``). Jobs that
        used the prompt keep their own prompt text in ``generation_jobs``, so the
        history is unaffected.
        """
        scope = _require_workspace(workspace_id)
        self.get_prompt(scope, prompt_id)
        with self._db.write_tx() as tx:
            tx.execute("DELETE FROM saved_prompts WHERE id = ? AND workspace_id = ?", (prompt_id, scope))

    # -- characters ---------------------------------------------------------
    def list_characters(
        self, workspace_id: str, *, q: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Live characters in one workspace, with their reference counts.

        Authorises: reading this workspace's characters and how many reference
        images each one carries.

        Refuses: another workspace's characters and soft-deleted ones; a page
        larger than 200 (clamped).
        """
        scope = _require_workspace(workspace_id)
        clauses = ["c.workspace_id = ?", "c.deleted_at IS NULL"]
        params: List[Any] = [scope]
        if q is not None and _text(q):
            pattern = _like(_text(q) or "")
            clauses.append("(c.name LIKE ? ESCAPE '\\' OR c.description LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        params.append(int(clamp(int(limit), 1, 200)))
        sql = (
            "SELECT c.*, (SELECT COUNT(*) FROM character_references r"
            " WHERE r.character_id = c.id) AS reference_count"
            " FROM characters c WHERE " + " AND ".join(clauses) +
            " ORDER BY c.updated_at DESC LIMIT ?"
        )
        return self._all(sql, params, table="characters")

    def get_character(self, workspace_id: str, character_id: str) -> Dict[str, Any]:
        """One character with its ordered reference images.

        Authorises: reading a character of this workspace and the asset ids it
        references.

        Refuses: a character that is missing, soft-deleted, or foreign
        (``NOT_FOUND``, identically). References are read through, not joined
        blindly: only assets in the same workspace are returned, so a stale
        cross-workspace row cannot leak an asset id.
        """
        scope = _require_workspace(workspace_id)
        character = self._scoped_row(
            "SELECT * FROM characters WHERE id = ? AND workspace_id = ? AND deleted_at IS NULL",
            (character_id, scope),
            "character",
            table="characters",
        )
        character["references"] = rows_to_dicts(
            self._db.query(
                "SELECT r.id, r.asset_id, r.role, r.position, r.notes, r.created_at"
                " FROM character_references r JOIN assets a ON a.id = r.asset_id"
                " WHERE r.character_id = ? AND a.workspace_id = ?"
                " ORDER BY r.position ASC",
                (character_id, scope),
            )
        )
        return character

    def create_character(
        self, workspace_id: str, user_id: str, payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Create a character in this workspace.

        Authorises: inserting a character attributed to ``user_id``; a
        ``parent_character_id`` may be given only if that parent is a live
        character of this same workspace, which is how refining a character makes
        a new version instead of destroying the old one.

        Refuses: an unknown field, a missing name, and a parent from another
        workspace (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        owner = _require_text(user_id, "user_id")
        values, row_id, echoed_owner = _absorb_row(
            _mapping(payload, "character"), _LIBRARY_ALIASES, "character", scope=scope
        )
        owner = echoed_owner or owner
        _reject_unknown(values, _CHARACTER_FIELDS, "character")
        parent = _text(values.get("parent_character_id"))
        if parent is not None and not self._db.exists(
            "characters", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (parent, scope)
        ):
            raise _not_found("character")
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "created_by_user_id": owner,
            "name": _require_text(values.get("name"), "name", limit=200),
            "description": truncate(str(values.get("description") or ""), 4000) or "",
            "attributes_json": json_dumps(_json_payload(values.get("attributes"), "attributes")),
            "prompt_details": truncate(str(values.get("prompt_details") or ""), 20000) or "",
            "preferred_models_json": json_dumps(
                _json_payload(values.get("preferred_models"), "preferred_models")
            ),
            "tags_json": json_dumps(_tag_list(values.get("tags"))),
            "version": 1,
            "parent_character_id": parent,
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert("characters", record)
        return self.get_character(scope, inserted["id"])

    def update_character(
        self, workspace_id: str, character_id: str, fields: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Change a character's description or attributes.

        Authorises: updating name, description, attributes, prompt details,
        preferred models and tags on a live character of this workspace.

        Refuses: an unknown field, ``version`` and ``parent_character_id`` (a new
        version is a new row, not an edit), and a character that is missing,
        deleted, or foreign (``NOT_FOUND``).
        """
        scope = _require_workspace(workspace_id)
        values, _row, _owner = _absorb_row(
            _mapping(fields, "character"), _LIBRARY_ALIASES, "character", scope=scope
        )
        _reject_unknown(values, _CHARACTER_FIELDS, "character")
        self.get_character(scope, character_id)
        updates: Dict[str, Any] = {}
        if "name" in values:
            updates["name"] = _require_text(values.get("name"), "name", limit=200)
        if "description" in values:
            updates["description"] = truncate(str(values.get("description") or ""), 4000) or ""
        if "attributes" in values:
            updates["attributes_json"] = json_dumps(_json_payload(values.get("attributes"), "attributes"))
        if "prompt_details" in values:
            updates["prompt_details"] = (
                truncate(str(values.get("prompt_details") or ""), 20000) or ""
            )
        if "preferred_models" in values:
            updates["preferred_models_json"] = json_dumps(
                _json_payload(values.get("preferred_models"), "preferred_models")
            )
        if "tags" in values:
            updates["tags_json"] = json_dumps(_tag_list(values.get("tags")))
        if "parent_character_id" in values:
            raise ApiError(
                VALIDATION_FAILED,
                "A new version is a new character with a parent, not an edit.",
                field="parent_character_id",
            )
        if not updates:
            return self.get_character(scope, character_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                "characters",
                updates,
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (character_id, scope),
            )
        return self.get_character(scope, character_id)

    def soft_delete_character(self, workspace_id: str, character_id: str) -> None:
        """Retire a character without destroying its references.

        Authorises: stamping ``deleted_at`` on a live character of this workspace.

        Refuses: a character that is missing, already deleted, or foreign
        (``NOT_FOUND``). The reference rows survive, which is deliberate: a
        character a previous generation used should still be explainable after it
        is retired.
        """
        scope = _require_workspace(workspace_id)
        self.get_character(scope, character_id)
        with self._db.write_tx() as tx:
            self._db.update_where(
                "characters",
                {"deleted_at": now_iso()},
                "id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (character_id, scope),
            )

    def add_character_reference(
        self,
        workspace_id: str,
        character_id: str,
        asset_id: str,
        *,
        role: str = "identity",
        notes: str = "",
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Attach a reference image to a character.

        Authorises: inserting a ``character_references`` row, provided both the
        character and the asset belong to this workspace.

        Refuses: a character or asset that is missing, deleted, or foreign
        (``NOT_FOUND``); an empty asset id; a negative position; and a duplicate
        (character, asset) pair, which is already attached (``CONFLICT``). The
        role is free text in the schema and is stored as given, so a role the
        studio does not know about cannot masquerade as one it does.
        """
        scope = _require_workspace(workspace_id)
        self.get_character(scope, character_id)
        asset = _require_text(asset_id, "asset_id")
        if not self._db.exists(
            "assets", "id = ? AND workspace_id = ? AND deleted_at IS NULL", (asset, scope)
        ):
            raise _not_found("asset")
        slot = _optional_int(position)
        if slot is None:
            slot = int(
                self._db.scalar(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM character_references"
                    " WHERE character_id = ?",
                    (character_id,),
                    default=0,
                )
                or 0
            )
        if slot < 0:
            raise ApiError(VALIDATION_FAILED, "position cannot be negative.", field="position")
        duplicate = self._db.exists(
            "character_references", "character_id = ? AND asset_id = ?", (character_id, asset)
        )
        if duplicate:
            raise ApiError(
                CONFLICT,
                "That image is already a reference for this character.",
                field="asset_id",
            )
        record = {
            "id": new_id(),
            "character_id": character_id,
            "asset_id": asset,
            "role": _text(role) or "identity",
            "position": slot,
            "notes": truncate(str(notes or ""), 1000) or "",
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert("character_references", record)
        return _shape_row("character_references", inserted) or {}

    def remove_character_reference(
        self, workspace_id: str, character_id: str, reference_id: str
    ) -> None:
        """Detach one reference image from a character.

        Authorises: deleting a ``character_references`` row whose character is a
        live character of this workspace.

        Refuses: a character that is missing, deleted, or foreign (``NOT_FOUND``),
        and a reference id that is not attached to *that* character
        (``NOT_FOUND``), so a reference id from another character -- or another
        workspace -- cannot be deleted by guessing it. The asset itself is not
        touched.
        """
        scope = _require_workspace(workspace_id)
        self.get_character(scope, character_id)
        with self._db.write_tx() as tx:
            removed = tx.execute(
                "DELETE FROM character_references WHERE id = ? AND character_id = ?",
                (reference_id, character_id),
            ).rowcount
        if not removed:
            raise _not_found("character reference")


# ---------------------------------------------------------------------------
# Usage and audit
# ---------------------------------------------------------------------------

#: Payload keys accepted by :meth:`UsageRepository.record`.
_USAGE_FIELDS: Tuple[str, ...] = (
    "user_id",
    "job_id",
    "connection_id",
    "model_id",
    "billing_party",
    "modality",
    "units",
    "estimated_cost",
    "actual_cost",
)


def _cost_amount(payload: Any) -> Optional[Tuple[str, float]]:
    """The (currency, amount) a cost payload states, or ``None`` when it reports none.

    Three shapes appear in this codebase: the ``CostEstimate.to_public()`` dict, a
    bare number, and an empty object meaning "nothing was reported".

    Two cases return ``None`` even though an ``amount`` key exists, both for the same
    reason -- a number in the payload is not the same thing as a cost that somebody
    observed:

    * ``is_billable`` is explicitly false. A provider that says "this run billed
      nothing" has reported the absence of a charge, not a charge of zero; writing
      zero into the total would make an unspent record indistinguishable from a
      free one when the summary is read later.
    * The currency is empty, or the payload is a bare number with no currency at
      all. An amount with no unit cannot be added to a currency total -- that is
      the same mistake as adding dollars to credits -- so it is left in
      ``unknown_cost_records`` where the note can explain it.

    An absent amount is likewise *not* zero: treating it as zero would turn "we do
    not know" into a free generation on an invoice.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        return _cost_amount(json_loads(payload, default=None))
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        # A bare number carries no currency, so it cannot be added to any total.
        return None
    if isinstance(payload, Mapping):
        amount = payload.get("amount")
        if amount is None:
            return None
        if payload.get("is_billable") is False:
            return None
        currency = _text(payload.get("currency"))
        if not currency:
            return None
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return None
        return (currency, value)
    return None


class UsageRepository(_Repository):
    """What was used, by whom, and on whose account.

    The rule here is one sentence long: a number that nobody observed is not a
    number. :meth:`summary` reports what it summed and, separately, how many
    records it could not include, so an operator can never mistake an incomplete
    total for a complete one.
    """

    TABLE = "usage_records"

    def record(self, workspace_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Append one usage record.

        Authorises: inserting a row that says who pays, for what, and every unit
        the platform actually observed. ``job_id``, ``connection_id`` and
        ``model_id`` may be given only when they belong to this workspace.

        Refuses: an unknown field; a billing party outside
        user_provider/platform, because billing a line to nobody is worse than
        refusing it; and a job, connection or model id from another workspace
        (``NOT_FOUND``). It refuses to invent an ``actual_cost``: an absent value
        stays NULL rather than becoming 0.
        """
        scope = _require_workspace(workspace_id)
        values, row_id, _unused_owner = _absorb_row(
            _mapping(payload, "usage"), _USAGE_ALIASES, "usage", scope=scope
        )
        _reject_unknown(values, _USAGE_FIELDS, "usage")
        party = _choice(values.get("billing_party"), BILLING_PARTIES, "billing_party")
        job_id = _text(values.get("job_id"))
        if job_id is not None and not self._db.exists(
            "generation_jobs", "id = ? AND workspace_id = ?", (job_id, scope)
        ):
            raise _not_found("job")
        connection_id = _text(values.get("connection_id"))
        if connection_id is not None and not self._db.exists(
            "api_connections", "id = ? AND workspace_id = ?", (connection_id, scope)
        ):
            raise _not_found("connection")
        model_id = _text(values.get("model_id"))
        if model_id is not None and not self._db.exists(
            "models", "id = ? AND workspace_id = ?", (model_id, scope)
        ):
            raise _not_found("model")
        record = {
            "id": row_id or new_id(),
            "workspace_id": scope,
            "user_id": _text(values.get("user_id")),
            "job_id": job_id,
            "connection_id": connection_id,
            "model_id": model_id,
            "billing_party": party,
            "modality": _text(values.get("modality")) or "image",
            "units_json": json_dumps(_json_payload(values.get("units"), "units")),
            "estimated_cost_json": json_dumps(
                _json_payload(values.get("estimated_cost"), "estimated_cost")
            ),
            "actual_cost_json": (
                None
                if values.get("actual_cost") is None
                else json_dumps(_json_payload(values.get("actual_cost"), "actual_cost"))
            ),
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert(self.TABLE, record)
        return _shape_row(self.TABLE, inserted) or {}

    def list(self, workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """The most recent usage records for one workspace.

        Authorises: reading this workspace's usage history.

        Refuses: another workspace's records; a page larger than 200 (clamped) so
        a report cannot be used to exhaust the process.
        """
        scope = _require_workspace(workspace_id)
        sql = (
            "SELECT * FROM usage_records WHERE workspace_id = ?"
            " ORDER BY created_at DESC LIMIT ?"
        )
        return self._all(sql, (scope, int(clamp(int(limit), 1, 200))))

    def summary(self, workspace_id: str) -> Dict[str, Any]:
        """Totals this workspace's usage, counting only costs somebody reported.

        Authorises: reading and summing the cost columns for this workspace.

        Refuses: to invent a cost. A record whose ``actual_cost_json`` and
        ``estimated_cost_json`` both state no amount contributes **nothing**, and
        is counted in ``unknown_cost_records`` instead; the response says so with
        ``costs_are_partial`` and a ``note``, so a partial total can never be
        presented as a complete one. Costs in different currencies are summed
        separately rather than added together, because adding dollars to credits
        would be the same lie in a different place. Refuses nothing else: an empty
        workspace is a zero-record summary, not an error.
        """
        scope = _require_workspace(workspace_id)
        rows = rows_to_dicts(
            self._db.query(
                "SELECT billing_party, modality, actual_cost_json, estimated_cost_json"
                " FROM usage_records WHERE workspace_id = ?",
                (scope,),
            )
        )
        totals: Dict[str, float] = {}
        records_with_cost = 0
        unknown = 0
        by_party: Dict[str, Dict[str, Any]] = {}
        by_modality: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            stated = _cost_amount(_json(row.get("actual_cost_json"), default=None))
            source = "actual"
            if stated is None:
                stated = _cost_amount(_json(row.get("estimated_cost_json"), default=None))
                source = "estimated"
            party = str(row.get("billing_party") or "unknown")
            modality = str(row.get("modality") or "unknown")
            party_bucket = by_party.setdefault(
                party, {"records": 0, "records_with_cost": 0, "unknown_cost_records": 0}
            )
            modality_bucket = by_modality.setdefault(
                modality, {"records": 0, "records_with_cost": 0, "unknown_cost_records": 0}
            )
            party_bucket["records"] += 1
            modality_bucket["records"] += 1
            if stated is None:
                unknown += 1
                party_bucket["unknown_cost_records"] += 1
                modality_bucket["unknown_cost_records"] += 1
                continue
            currency, amount = stated
            totals[currency] = totals.get(currency, 0.0) + amount
            records_with_cost += 1
            party_bucket["records_with_cost"] += 1
            modality_bucket["records_with_cost"] += 1
            bucket_key = "%s:%s" % (currency, source)
            party_bucket.setdefault("costs", {})
            party_bucket["costs"][bucket_key] = (
                party_bucket["costs"].get(bucket_key, 0.0) + amount
            )
            modality_bucket.setdefault("costs", {})
            modality_bucket["costs"][bucket_key] = (
                modality_bucket["costs"].get(bucket_key, 0.0) + amount
            )
        costs = [
            {"currency": currency or None, "amount": round(amount, 6)}
            for currency, amount in sorted(totals.items())
        ]
        partial = unknown > 0
        if not rows:
            # An empty workspace has stated nothing. Saying "every record stated a
            # cost" over zero records is vacuously true and reads as a claim that
            # there are costs -- a zero invoice dressed as a complete one.
            note = "No usage has been recorded yet, so there is nothing to total."
        elif partial:
            note = (
                "No cost was reported for %d of %d records, so these totals are a "
                "lower bound rather than an invoice." % (unknown, len(rows))
            )
        else:
            note = "Every one of the %d records stated a cost." % len(rows)
        return {
            "workspace_id": scope,
            "records": len(rows),
            "records_with_cost": records_with_cost,
            "unknown_cost_records": unknown,
            "costs": costs,
            "costs_are_partial": partial,
            "currencies": [item["currency"] for item in costs],
            "by_billing_party": by_party,
            "by_modality": by_modality,
            "note": note,
        }


class AuditRepository(_Repository):
    """The audit log: an append-only record of what was done, to what, by whom.

    Nothing in this class updates or deletes a row. That is not a limitation to be
    fixed later: an audit trail with an edit history is not an audit trail.
    """

    TABLE = "audit_log"

    def record(
        self,
        workspace_id: str,
        user_id: Optional[str],
        action: str,
        target_type: str = "",
        target_id: str = "",
        request_id: str = "",
        ip_hash: str = "",
        detail: Any = None,
    ) -> Dict[str, Any]:
        """Append one audit entry.

        Authorises: inserting a row naming the action, the target, the request that
        caused it, and a detail payload.

        Refuses: an unknown action (an empty one), because an unlabelled entry
        answers no question; and an empty workspace id, since an entry nobody can
        find is not an audit. It stores the caller's ``ip_hash`` verbatim and never
        an address: hashing is the caller's job (see :mod:`studio.security`), and
        this layer would have no way to know whether the value it was given was a
        hash or an address.
        """
        scope = _require_workspace(workspace_id)
        label = _require_text(action, "action", limit=120)
        record = {
            "id": new_id(),
            "workspace_id": scope,
            "user_id": _text(user_id),
            "action": label,
            "target_type": truncate(str(target_type or ""), 120) or "",
            "target_id": truncate(str(target_id or ""), 200) or "",
            "request_id": truncate(str(request_id or ""), 200) or "",
            "ip_hash": truncate(str(ip_hash or ""), 200) or "",
            "detail_json": json_dumps(_json_payload(detail, "detail")),
        }
        with self._db.write_tx() as tx:
            inserted = tx.insert(self.TABLE, record)
        return _shape_row(self.TABLE, inserted) or {}

    def list(self, workspace_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """The most recent audit entries for one workspace, newest first.

        Authorises: reading this workspace's audit trail.

        Refuses: another workspace's entries, and a page larger than 200 (clamped).
        An entry with a NULL workspace is never returned -- those are
        deployment-level events, and this method is scoped to a tenant.
        """
        scope = _require_workspace(workspace_id)
        sql = (
            "SELECT * FROM audit_log WHERE workspace_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        )
        return self._all(sql, (scope, int(clamp(int(limit), 1, 200))))


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Repositories:
    """Every repository bound to one database handle.

    Frozen so a request handler cannot swap the job queue for something else
    halfway through a transaction, and built once per process rather than once per
    call, so the schema's column cache is shared.
    """

    connections: ConnectionRepository
    models: ModelRepository
    projects: ProjectRepository
    assets: AssetRepository
    jobs: JobRepository
    library: LibraryRepository
    usage: UsageRepository
    audit: AuditRepository


def repositories(db: Database) -> Repositories:
    """Bind every repository to ``db``.

    Authorises: nothing by itself -- each method on the result does its own
    checking. This function only wires the aggregate repositories to one database
    handle, so a request-scoped caller has a single object to depend on instead of
    eight constructors.

    Refuses: to construct anything without a usable database handle; a non-Database
    argument raises ``TypeError`` here rather than a confusing ``AttributeError`
    several calls later.
    """
    if not isinstance(db, Database):
        raise TypeError("repositories() needs a studio.db.Database, got %r" % (type(db).__name__,))
    return Repositories(
        connections=ConnectionRepository(db),
        models=ModelRepository(db),
        projects=ProjectRepository(db),
        assets=AssetRepository(db),
        jobs=JobRepository(db),
        library=LibraryRepository(db),
        usage=UsageRepository(db),
        audit=AuditRepository(db),
    )
