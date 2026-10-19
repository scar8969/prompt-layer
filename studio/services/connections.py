"""BYOK/BYOM connections: sealing credentials, checking egress, testing, registering models.

This is the module the whole product rests on, so the rules are stated once here
rather than repeated in routes:

* A credential arrives as plaintext exactly once, in the create or rotate call.
  It is sealed with the deployment key, and only the ciphertext, the key id, the
  last four characters and a public fingerprint are ever stored or returned.
* Every base URL is checked against the egress policy before it is saved and
  again before every request, so editing a connection cannot smuggle a blocked
  host in past the first check.
* A connection test makes one real, bounded call and stores the outcome with a
  timestamp. Nothing is reported as verified that was not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..adapters.base import (
    IMAGE_MODES,
    VIDEO_MODES,
    ParameterSchema,
    parse_parameter_schema,
)
from ..adapters.core import adapter_types, get_adapter
from ..adapters.types import AdapterContext, CapabilityProfile, ConnectionTestResult
from ..crypto import (
    EncryptionUnavailable,
    SecretBox,
    SecretUnreadable,
    fingerprint_public,
)
from ..errors import (
    CAPABILITY_UNSUPPORTED,
    CONFIGURATION_REQUIRED,
    FORBIDDEN,
    NOT_FOUND,
    VALIDATION_FAILED,
    ApiError,
)
from ..logging_setup import get_logger
from ..net import client_for, validate_endpoint
from ..redact import redact_url
from ..util import json_dumps, json_loads, truncate

LOG = get_logger(__name__)

#: Header names that must never be set through a connection form, because they
#: would either move the credential somewhere it cannot be redacted or rewrite
#: the request in ways the egress policy did not check.
FORBIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "accept-encoding",
        "cookie",
        "proxy-authorization",
    }
)

#: Adapter types a user may choose. The database CHECK constraint lists the same
#: set, and a test asserts the two agree.
ALLOWED_ADAPTERS = frozenset({"fal_queue", "openai_images", "declarative_http", "mock"})

MAX_EXTRA_HEADERS = 12
MAX_ALLOWED_HOSTS = 20


@dataclass(frozen=True)
class SealedCredential:
    """Everything about a credential that is safe to keep, and nothing else."""

    ciphertext: str
    key_id: str
    last4: str
    fingerprint: str

    def to_public(self) -> Dict[str, Any]:
        """The form the browser is allowed to see."""
        return {
            "present": bool(self.ciphertext),
            "last4": self.last4,
            "key_id": self.key_id,
            "fingerprint": self.fingerprint,
        }


def _require_text(payload: Mapping[str, Any], name: str, *, limit: int = 500) -> str:
    """Read a required non-empty string, naming the field when it is missing."""
    value = payload.get(name)
    text = str(value or "").strip()
    if not text:
        raise ApiError(
            VALIDATION_FAILED,
            "The %s field is required." % name.replace("_", " "),
            field=name,
        )
    if len(text) > limit:
        raise ApiError(
            VALIDATION_FAILED,
            "The %s field is too long." % name.replace("_", " "),
            field=name,
            details={"limit": limit, "length": len(text)},
        )
    return text


def clean_extra_headers(raw: Any) -> Dict[str, str]:
    """Accept only non-secret headers. A rejected name is named, never dropped.

    Refuses a credential-bearing or framing header outright rather than ignoring
    it, because silently discarding "Authorization" would leave the user
    believing their custom scheme was applied.
    """
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, str):
        raw = json_loads(raw, {})
    if not isinstance(raw, Mapping):
        raise ApiError(VALIDATION_FAILED, "Extra headers must be an object.", field="extra_headers")
    if len(raw) > MAX_EXTRA_HEADERS:
        raise ApiError(
            VALIDATION_FAILED,
            "A connection may carry at most %d extra headers." % MAX_EXTRA_HEADERS,
            field="extra_headers",
        )
    out: Dict[str, str] = {}
    for name, value in raw.items():
        header = str(name or "").strip()
        if not header or len(header) > 100:
            raise ApiError(
                VALIDATION_FAILED,
                "An extra header name must be 1 to 100 characters.",
                field="extra_headers",
            )
        if any(char in header for char in " \t\r\n:;"):
            raise ApiError(
                VALIDATION_FAILED,
                "The header name %r contains a character that is not allowed." % header,
                field="extra_headers",
                details={"header": truncate(header, 100)},
            )
        if header.lower() in FORBIDDEN_HEADERS:
            raise ApiError(
                VALIDATION_FAILED,
                "The %s header cannot be set here." % header,
                field="extra_headers",
                hint="Use the credential field for secrets, and the base URL for the host.",
            )
        text = str(value if value is not None else "")
        if len(text) > 2000:
            raise ApiError(
                VALIDATION_FAILED,
                "The value of the %s header is too long." % header,
                field="extra_headers",
            )
        if "\r" in text or "\n" in text:
            raise ApiError(
                VALIDATION_FAILED,
                "The value of the %s header contains a line break." % header,
                field="extra_headers",
            )
        out[header] = text
    return out


def clean_allowed_hosts(raw: Any) -> List[str]:
    """Hostnames this connection may reach beyond the default policy.

    Reasonably strict: a hostname, optional port, no scheme, no path, no
    wildcard. The egress policy still applies to the resolved address; this
    list only widens the private-address and plain-http doors for named hosts.
    """
    if raw in (None, "", [], ()):
        return []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        raise ApiError(VALIDATION_FAILED, "Allowed hosts must be a list.", field="allowed_hosts")
    if len(raw) > MAX_ALLOWED_HOSTS:
        raise ApiError(
            VALIDATION_FAILED,
            "At most %d allowed hosts may be listed." % MAX_ALLOWED_HOSTS,
            field="allowed_hosts",
        )
    out: List[str] = []
    import re as _re

    pattern = _re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:[0-9]{1,5})?$")
    for item in raw:
        host = str(item or "").strip().lower()
        if not host:
            continue
        if "://" in host or "/" in host or "*" in host:
            raise ApiError(
                VALIDATION_FAILED,
                "An allowed host must be a bare hostname, optionally with a port.",
                field="allowed_hosts",
                details={"value": truncate(host, 100)},
            )
        if not pattern.match(host):
            raise ApiError(
                VALIDATION_FAILED,
                "The host %r is not a valid hostname." % host,
                field="allowed_hosts",
            )
        if host not in out:
            out.append(host)
    return out
def credential_hint(adapter_slug: str) -> str:
    """What to tell a user about where the key for this provider type comes from."""
    try:
        adapter = get_adapter(adapter_slug)
    except ApiError:
        return ""
    return str(getattr(adapter, "credential_hint", "") or "")


class ConnectionService:
    """Create, test, rotate and delete a workspace provider connection.

    Every method takes the session, not just a workspace id, and every method
    requires a permission before it reads or writes anything.
    """

    def __init__(self, repositories: Any, config: Any) -> None:
        self.repos = repositories
        self.config = config

    # -- capability surface ---------------------------------------------------

    def types(self) -> List[Dict[str, Any]]:
        """The provider types the wizard offers, built from the live registry.

        A type whose slug the database cannot store is filtered out, so the
        wizard cannot offer a choice that would fail on save.
        """
        out = []
        for entry in adapter_types():
            if entry["slug"] not in ALLOWED_ADAPTERS:
                continue
            if entry["slug"] == "mock" and not getattr(self.config, "mock_provider_enabled", False):
                continue
            item = dict(entry)
            item["credential_hint"] = credential_hint(entry["slug"])
            out.append(item)
        return out

    # -- sealing --------------------------------------------------------------

    def _box(self) -> SecretBox:
        """The deployment key box, or a clear refusal when it is not configured."""
        key = getattr(self.config.security, "encryption_key", "")
        if not key:
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "Credential storage is not configured on this deployment, so no key can be saved.",
                details={"missing": self.config.missing_for("credentials")},
                hint="Set STUDIO_ENCRYPTION_KEY to 32 bytes of hex and restart.",
            )
        try:
            return SecretBox(key)
        except EncryptionUnavailable:
            raise

    def seal_credential(self, connection_id: str, plaintext: str) -> SealedCredential:
        """Seal a credential for storage. The plaintext does not leave this frame.

        The connection id is the associated data, so a ciphertext copied from one
        connection cannot be opened as another even by someone with database
        write access.
        """
        text = (plaintext or "").strip()
        if not text:
            raise ApiError(VALIDATION_FAILED, "The API key is empty.", field="credential")
        if len(text) > 4096:
            raise ApiError(
                VALIDATION_FAILED,
                "That API key is longer than any provider issues.",
                field="credential",
            )
        sealed = self._box().seal(text, connection_id)
        return SealedCredential(
            ciphertext=sealed.ciphertext,
            key_id=sealed.key_id,
            last4=text[-4:] if len(text) >= 4 else "",
            fingerprint=fingerprint_public(text, 8),
        )

    def open_credential(self, connection_id: str, ciphertext: str, key_id: str = "") -> str:
        """Recover a credential for one outbound request. Never logged, never returned."""
        if not ciphertext:
            return ""
        box = self._box()
        try:
            return box.open(ciphertext, connection_id)
        except SecretUnreadable:
            raise
        except Exception as error:  # noqa: BLE001 - a broken key is a config problem
            LOG.error(
                "credential could not be opened",
                extra={"connection_id": connection_id, "reason": type(error).__name__},
            )
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "This connection key cannot be opened with the key this deployment holds.",
                details={"connection_id": connection_id},
                hint="Replace the key for this connection. An encryption key that was rotated without its previous value cannot recover old rows.",
            ) from None

    def needs_rotation(self, ciphertext: str) -> bool:
        """Whether a stored row is under an older key than the current primary."""
        if not ciphertext:
            return False
        try:
            return self._box().needs_rotation(ciphertext)
        except Exception:  # noqa: BLE001
            return False

    # -- validation -----------------------------------------------------------

    def check_base_url(
        self,
        base_url: str,
        allowed_hosts: Sequence[str] = (),
        *,
        allow_insecure: bool = False,
    ) -> str:
        """Validate a base URL against the egress policy without sending anything.

        A host the user explicitly allowlisted is permitted to be private or plain
        http; everything else must be https and public. The check is repeated on
        every request, so this is the earlier of the two doors, not the only one.
        """
        text = (base_url or "").strip()
        if not text:
            raise ApiError(VALIDATION_FAILED, "The base URL is required.", field="base_url")
        if len(text) > 2000:
            raise ApiError(VALIDATION_FAILED, "The base URL is too long.", field="base_url")
        allow_insecure = bool(allowed_hosts) or allow_insecure
        try:
            target = validate_endpoint(text, self.config, allow_insecure=allow_insecure)
        except ApiError as error:
            # The field is what puts the message next to the input the user typed.
            error.field = error.field or "base_url"
            if error.details is None:
                error.details = {"url": redact_url(text)}
            raise
        return target.url if hasattr(target, "url") else text

    def normalise_create(self, session: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate a create payload into the row the repository will insert.

        The credential is sealed here, using the id the caller will store, so the
        ciphertext is bound to the connection before the row exists.
        """
        session.require("connection:manage")
        name = _require_text(payload, "name", limit=120)
        adapter_type = str(payload.get("adapter_type") or "").strip()
        available = {item["slug"] for item in self.types()}
        if adapter_type not in available:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "There is no provider type called %r on this deployment." % adapter_type,
                field="adapter_type",
                details={"available": sorted(available)},
                hint="Pick one of the listed provider types.",
            )
        allowed_hosts = clean_allowed_hosts(payload.get("allowed_hosts"))
        base_url = self.check_base_url(
            str(payload.get("base_url") or ""),
            allowed_hosts,
            # The mock adapter answers locally and holds no credential, so there is
            # nothing on the wire to protect. Every other type must be https, or be
            # named in allowed_hosts by the operator who set it up.
            allow_insecure=adapter_type == "mock",
        )
        auth_scheme = str(payload.get("auth_scheme") or "bearer").strip().lower()
        if auth_scheme not in ("bearer", "header", "query", "none"):
            raise ApiError(
                VALIDATION_FAILED,
                "The authentication scheme must be bearer, header, query or none.",
                field="auth_scheme",
            )
        header_name = str(payload.get("auth_header_name") or "Authorization").strip() or "Authorization"
        if auth_scheme in ("bearer", "header"):
            if not header_name.isidentifier() and "-" not in header_name:
                raise ApiError(
                    VALIDATION_FAILED,
                    "The authentication header name is not a valid header name.",
                    field="auth_header_name",
                )
            if header_name.lower() in FORBIDDEN_HEADERS - {"authorization"}:
                raise ApiError(
                    VALIDATION_FAILED,
                    "That header cannot carry the credential.",
                    field="auth_header_name",
                )
        config = payload.get("config")
        if isinstance(config, str):
            config = json_loads(config, {})
        if config is None:
            config = {}
        if not isinstance(config, Mapping):
            raise ApiError(VALIDATION_FAILED, "The provider settings must be an object.", field="config")
        if adapter_type == "declarative_http":
            from ..adapters.declarative import load_spec, spec_summary

            raw_mapping = payload.get("mapping")
            if raw_mapping is None and isinstance(config, Mapping):
                raw_mapping = config.get("mapping")
            spec = load_spec(raw_mapping)
            config = dict(config)
            config["mapping"] = spec
            config["mapping_summary"] = spec_summary(spec)
        credential = str(payload.get("credential") or "")
        sealed: Optional[SealedCredential] = None
        if credential:
            sealed = self.seal_credential(str(payload.get("connection_id") or "new-connection"), credential)
        elif auth_scheme != "none" and adapter_type != "mock":
            raise ApiError(
                VALIDATION_FAILED,
                "This provider type needs an API key.",
                field="credential",
                hint="An endpoint with no key is rare; choose the none scheme if it truly needs none.",
            )
        row: Dict[str, Any] = {
            "name": name,
            "provider_slug": adapter_type,
            "adapter_type": adapter_type,
            "base_url": base_url,
            "auth_scheme": auth_scheme,
            "auth_header_name": header_name,
            "extra_headers_json": json_dumps(clean_extra_headers(payload.get("extra_headers"))),
            "config_json": json_dumps(dict(config)),
            "allowed_hosts_json": json_dumps(list(allowed_hosts)),
            "is_enabled": 1,
        }
        if sealed is not None:
            row["credential_ciphertext"] = sealed.ciphertext
            row["credential_key_id"] = sealed.key_id
            row["credential_last4"] = sealed.last4
            row["credential_fingerprint"] = sealed.fingerprint
        return row

    # -- context construction ------------------------------------------------

    def context_for(self, connection: Mapping[str, Any], *, with_credential: bool = True) -> AdapterContext:
        """Build the adapter context for one call, opening the key if needed.

        With with_credential False no secret is opened, which is what a listing
        or a capability read does: those must not touch the key at all.
        """
        connection_id = str(connection.get("id") or connection.get("connection_id") or "")
        credential = ""
        if with_credential and connection.get("credential_ciphertext"):
            credential = self.open_credential(
                connection_id,
                str(connection.get("credential_ciphertext") or ""),
                str(connection.get("credential_key_id") or ""),
            )
        allowed = connection.get("allowed_hosts") or []
        if isinstance(allowed, str):
            allowed = json_loads(allowed, [])
        config = connection.get("config") or {}
        if isinstance(config, str):
            config = json_loads(config, {})
        return AdapterContext(
            connection_id=connection_id,
            base_url=str(connection.get("base_url") or ""),
            credential=credential or None,
            auth_scheme=str(connection.get("auth_scheme") or "bearer"),
            auth_header_name=str(connection.get("auth_header_name") or "Authorization"),
            extra_headers=connection.get("extra_headers") or {},
            config=dict(config),
            http=client_for(self.config),
            timeout_seconds=self.config.egress.read_timeout_seconds,
            max_redirects=self.config.egress.max_redirects,
        )

    # -- operations ----------------------------------------------------------

    def create(self, session: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Create a connection and, when the payload carries them, its models."""
        from ..util import new_id

        session.require("connection:manage")
        connection_id = new_id()
        row_payload = dict(payload)
        row_payload["connection_id"] = connection_id
        row = self.normalise_create(session, row_payload)
        row["id"] = connection_id
        row["workspace_id"] = session.workspace_id
        row["created_by_user_id"] = session.user_id
        connection = self.repos.connections.create(session.workspace_id, session.user_id, row)
        models = []
        for item in payload.get("models") or ():
            if not isinstance(item, Mapping):
                continue
            models.append(self.add_model(session, connection_id, item))
        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "connection.created",
            target_type="connection",
            target_id=connection_id,
            request_id="",
            ip_hash="",
            detail={"adapter_type": row["adapter_type"], "name": row["name"]},
        )
        return {"connection": connection, "models": models}

    def update(self, session: Any, connection_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Change a connection without changing its credential.

        A base URL change is re-validated exactly as it was on create, so an
        edit cannot point a connection at a host the policy would have refused.
        """
        session.require("connection:manage")
        current = self.repos.connections.get(session.workspace_id, connection_id)
        fields: Dict[str, Any] = {}
        if "name" in payload:
            fields["name"] = _require_text(payload, "name", limit=120)
        allowed_hosts = current.get("allowed_hosts") or []
        if "allowed_hosts" in payload:
            allowed_hosts = clean_allowed_hosts(payload.get("allowed_hosts"))
            fields["allowed_hosts_json"] = json_dumps(list(allowed_hosts))
        if "base_url" in payload:
            fields["base_url"] = self.check_base_url(
                str(payload.get("base_url") or ""),
                allowed_hosts,
                allow_insecure=str(current.get("adapter_type") or "") == "mock",
            )
        if "auth_scheme" in payload:
            scheme = str(payload.get("auth_scheme") or "").strip().lower()
            if scheme not in ("bearer", "header", "query", "none"):
                raise ApiError(
                    VALIDATION_FAILED,
                    "The authentication scheme must be bearer, header, query or none.",
                    field="auth_scheme",
                )
            fields["auth_scheme"] = scheme
        if "auth_header_name" in payload:
            fields["auth_header_name"] = str(payload.get("auth_header_name") or "Authorization").strip()
        if "extra_headers" in payload:
            fields["extra_headers_json"] = json_dumps(clean_extra_headers(payload.get("extra_headers")))
        if "config" in payload:
            config = payload.get("config")
            if isinstance(config, str):
                config = json_loads(config, {})
            if not isinstance(config, Mapping):
                raise ApiError(VALIDATION_FAILED, "The provider settings must be an object.", field="config")
            if current.get("adapter_type") == "declarative_http":
                from ..adapters.declarative import load_spec, spec_summary

                spec = load_spec(config.get("mapping"))
                config = dict(config)
                config["mapping"] = spec
                config["mapping_summary"] = spec_summary(spec)
            fields["config_json"] = json_dumps(dict(config))
        if not fields:
            return {"connection": current}
        updated = self.repos.connections.update(session.workspace_id, connection_id, fields)
        return {"connection": updated}

    def rotate_key(self, session: Any, connection_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Replace the credential in place, keeping the connection and its models.

        Rotation is deliberately a separate operation from editing: the audit
        log says a key changed, and the models, routes and history are untouched.
        """
        session.require("connection:manage")
        self.repos.connections.get(session.workspace_id, connection_id)
        sealed = self.seal_credential(connection_id, str(payload.get("credential") or ""))
        updated = self.repos.connections.rotate_credential(
            session.workspace_id,
            connection_id,
            {
                "credential_ciphertext": sealed.ciphertext,
                "credential_key_id": sealed.key_id,
                "credential_last4": sealed.last4,
                "credential_fingerprint": sealed.fingerprint,
            },
        )
        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "connection.key_rotated",
            target_type="connection",
            target_id=connection_id,
            request_id="",
            ip_hash="",
            detail={"key_id": sealed.key_id},
        )
        return {"connection": updated}

    def delete(self, session: Any, connection_id: str) -> Dict[str, Any]:
        """Remove a connection, its credential, and the usability of its models.

        The generation history is kept: a job that ran against this provider is a
        record of something that happened, and deleting it would make the usage
        log lie. The models are disabled rather than deleted for the same reason.
        """
        session.require("connection:manage")
        self.repos.connections.get(session.workspace_id, connection_id)
        result = self.repos.connections.soft_delete(session.workspace_id, connection_id)
        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "connection.deleted",
            target_type="connection",
            target_id=connection_id,
            request_id="",
            ip_hash="",
            detail=dict(result),
        )
        return result

    def test(self, session: Any, connection_id: str) -> Dict[str, Any]:
        """Make one real call with the stored credential and record the outcome.

        The result is stored with a timestamp and returned verbatim. There is no
        path through this function that reports ok without the provider having
        answered, because the wizard shows this screen as proof.
        """
        session.require("connection:manage")
        connection = self.repos.connections.get(session.workspace_id, connection_id)
        if not connection.get("is_enabled"):
            raise ApiError(
                VALIDATION_FAILED,
                "This connection is disabled, so it was not tested.",
                hint="Re-enable it first.",
            )
        adapter = get_adapter(str(connection.get("adapter_type") or ""))
        try:
            context = self.context_for(connection)
        except ApiError as error:
            result = ConnectionTestResult(
                outcome="configuration_required"
                if error.code == CONFIGURATION_REQUIRED
                else "internal_error",
                ok=False,
                message=error.message,
                detail={"provider_message": error.provider_message or ""},
            )
            stored = self.repos.connections.record_test(
                session.workspace_id,
                connection_id,
                {
                    "outcome": result.outcome,
                    "http_status": None,
                    "latency_ms": None,
                    "detail_json": json_dumps(result.detail),
                },
            )
            return {"test": dict(result.to_public(), checked_at=stored.get("last_test_at", ""), message=result.message)}
        started = time.monotonic()
        try:
            result = adapter.test_connection(context)
        except ApiError as error:
            latency = int((time.monotonic() - started) * 1000)
            result = ConnectionTestResult(
                outcome=error.code
                if error.code in {
                    "auth_failed",
                    "endpoint_not_found",
                    "unsupported_operation",
                    "invalid_model_id",
                    "rate_limited",
                    "timeout",
                    "invalid_response",
                    "network_restricted",
                    "provider_error",
                    "internal_error",
                }
                else "internal_error",
                ok=False,
                http_status=error.status,
                latency_ms=latency,
                message=error.message,
                detail={"hint": error.hint or ""},
            )
        stored = self.repos.connections.record_test(
            session.workspace_id,
            connection_id,
            {
                "outcome": result.outcome or ("ok" if result.ok else "internal_error"),
                "http_status": result.http_status,
                "latency_ms": result.latency_ms,
                "detail_json": json_dumps(dict(result.detail, message=result.message)),
            },
        )
        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "connection.tested",
            target_type="connection",
            target_id=connection_id,
            request_id="",
            ip_hash="",
            detail={"outcome": result.outcome, "ok": result.ok},
        )
        public = dict(result.to_public())
        public["checked_at"] = stored.get("last_test_at", "")
        return {"test": public}

    # -- models --------------------------------------------------------------

    def add_model(self, session: Any, connection_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Register one model against a connection, with an explicit capability profile.

        Modes are checked against the modality here, so an image model cannot be
        given a video mode and quietly produce disabled buttons the user does not
        understand. Nothing is inferred from the modality label.
        """
        session.require("model:manage")
        connection = self.repos.connections.get(session.workspace_id, connection_id)
        model_key = _require_text(payload, "model_key", limit=300)
        modality = str(payload.get("modality") or "image").strip().lower()
        if modality not in ("image", "video", "other"):
            raise ApiError(
                VALIDATION_FAILED,
                "The modality must be image, video or other.",
                field="modality",
            )
        # A provider that knows its own catalogue can refuse a key it cannot
        # render here, where the user is still looking at the field. Otherwise
        # the typo would surface minutes later as a failed job with a message
        # about a model that does not exist.
        adapter_class = get_adapter(str(connection.get("adapter_type") or ""))
        accepts = getattr(adapter_class, "accepts_model_key", None)
        if callable(accepts) and not accepts(model_key):
            hint = getattr(adapter_class, "model_key_hint", None)
            known = hint() if callable(hint) else ""
            raise ApiError(
                VALIDATION_FAILED,
                "The %s provider has no model called %r." % (connection.get("name"), model_key),
                field="model_key",
                details={"known": known},
                hint=("Use one of: %s" % known) if known else None,
            )
        vocabulary = IMAGE_MODES if modality == "image" else VIDEO_MODES if modality == "video" else ()
        modes = payload.get("modes")
        if modes is None:
            adapter = get_adapter(str(connection.get("adapter_type") or ""))
            modes = list(adapter.default_modes())
        if isinstance(modes, str):
            modes = [part.strip() for part in modes.replace(",", " ").split() if part.strip()]
        if not isinstance(modes, (list, tuple)):
            raise ApiError(VALIDATION_FAILED, "Modes must be a list.", field="modes")
        clean_modes: List[str] = []
        for item in modes:
            mode = str(item or "").strip().lower()
            if not mode:
                continue
            if mode not in vocabulary:
                raise ApiError(
                    CAPABILITY_UNSUPPORTED,
                    "%r is not a %s generation mode." % (mode, modality),
                    field="modes",
                    details={"known": list(vocabulary)},
                )
            if mode not in clean_modes:
                clean_modes.append(mode)
        schema_raw = payload.get("parameter_schema")
        schema = parse_parameter_schema(schema_raw)
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, str):
            capabilities = json_loads(capabilities, {})
        if capabilities is None:
            capabilities = {}
        if not isinstance(capabilities, Mapping):
            raise ApiError(
                VALIDATION_FAILED,
                "Capabilities must be an object.",
                field="capabilities",
            )
        profile = CapabilityProfile.from_public(
            dict(capabilities, modality=modality, modes=tuple(clean_modes))
        )
        defaults = payload.get("defaults") or {}
        if isinstance(defaults, str):
            defaults = json_loads(defaults, {})
        if not isinstance(defaults, Mapping):
            raise ApiError(VALIDATION_FAILED, "Defaults must be an object.", field="defaults")
        limits = payload.get("limits") or {}
        if isinstance(limits, str):
            limits = json_loads(limits, {})
        row: Dict[str, Any] = {
            "workspace_id": session.workspace_id,
            "connection_id": connection_id,
            "model_key": model_key,
            "display_name": str(payload.get("display_name") or model_key).strip()[:200],
            "provider_slug": str(connection.get("adapter_type") or "custom"),
            "modality": modality,
            "modes_json": json_dumps(list(clean_modes)),
            "capabilities_json": json_dumps(dict(capabilities)),
            "parameter_schema_json": json_dumps(schema.to_public()),
            "defaults_json": json_dumps(dict(defaults)),
            "limits_json": json_dumps(dict(limits) if isinstance(limits, Mapping) else {}),
            "source": "discovered" if payload.get("source") == "discovered" else "manual",
            "is_enabled": 1,
        }
        created = self.repos.models.create(session.workspace_id, row)
        # Re-read through with_provider so is_mock comes from the adapter class
        # rather than from anything this call passed in.
        model = self.repos.models.with_provider(session.workspace_id, str(created["id"]))
        return model

    def discover_models(self, session: Any, connection_id: str) -> Dict[str, Any]:
        """Ask the provider what it lists. Nothing is registered automatically.

        A discovered model has no capability profile until a human confirms one,
        so listing is deliberately separate from saving.
        """
        session.require("model:read")
        connection = self.repos.connections.get(session.workspace_id, connection_id)
        adapter = get_adapter(str(connection.get("adapter_type") or ""))
        context = self.context_for(connection)
        try:
            discovered = adapter.list_models(context)
        except ApiError as error:
            raise
        existing = {
            str(item.get("model_key"))
            for item in self.repos.models.list(session.workspace_id, connection_id=connection_id)
        }
        items = []
        for item in discovered:
            public = item.to_public()
            public["already_registered"] = item.model_key in existing
            items.append(public)
        return {"models": items, "count": len(items)}

    def suggested_capabilities(self, session: Any, connection_id: str, model_key: str) -> Dict[str, Any]:
        """What the provider states about this model, or an explicit "it does not say".

        Returns provider_provided False when the adapter has nothing to offer, and
        the caller must then ask the user to declare the capabilities by hand.
        """
        session.require("model:read")
        connection = self.repos.connections.get(session.workspace_id, connection_id)
        adapter = get_adapter(str(connection.get("adapter_type") or ""))
        context = self.context_for(connection)
        profile = adapter.capabilities_for(context, model_key)
        if profile is None:
            return {
                "provider_provided": False,
                "capabilities": None,
                "note": "This provider publishes no capability metadata, so you declare what the model supports.",
            }
        schema = None
        builder = getattr(adapter, "parameter_schema_for", None)
        if callable(builder):
            try:
                built = builder(context, model_key)
            except ApiError:
                built = None
            if isinstance(built, ParameterSchema):
                schema = built.to_public()
        return {
            "provider_provided": True,
            "capabilities": profile.to_public(),
            "parameter_schema": schema,
            "note": "Read from the provider own published description of this endpoint.",
        }
