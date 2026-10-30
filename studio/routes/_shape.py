"""Shaping database rows into the JSON the client contract describes.

The repository returns rows with the column names the schema uses; the client
contract uses plain names. Rather than make every route remember to rename
`parameters_json`, the translation happens once, here, and the rule is stated:
a column that ends in `_json` is already parsed, so its value moves to the key
without the suffix. Nothing is invented and no column is dropped by accident,
because an unknown key is carried through unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

#: Columns whose value is a secret-shaped part of a connection. They are removed
#: before a connection ever leaves the server, so a new column cannot leak by
#: being forgotten in a route.
CONNECTION_SECRET_COLUMNS = (
    "credential_ciphertext",
    "credential_key_id",
    "credential_last4",
    "credential_fingerprint",
    "credential_updated_at",
)


def row(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """One row with its JSON columns unwrapped. None becomes an empty dict."""
    if not value:
        return {}
    out: Dict[str, Any] = {}
    for key, item in value.items():
        if key.endswith("_json"):
            out[key[: -len("_json")]] = item
        else:
            out[key] = item
    return out


def rows(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """A list of rows, each unwrapped."""
    return [row(value) for value in values or ()]


def connection(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A connection safe to send: no ciphertext, and a credential summary instead.

    The public shape carries only what the UI shows: whether a key is present,
    its last four characters, the key id and a fingerprint. The ciphertext never
    leaves the server, and neither does the key id used for opening it beyond
    what an operator needs to see that rotation happened.
    """
    if not value:
        return {}
    out = row(value)
    # The repository strips the ciphertext on every read path, and reports
    # has_credential instead. Either signal means "a key is stored".
    present = bool(out.pop("credential_ciphertext", "")) or bool(out.pop("has_credential", False))
    key_id = str(out.pop("credential_key_id", "") or "")
    last4 = str(out.pop("credential_last4", "") or "")
    fingerprint = str(out.pop("credential_fingerprint", "") or "")
    out.pop("credential_updated_at", None)
    out["credential"] = {
        "present": present,
        "last4": last4,
        "key_id": key_id,
        "fingerprint": fingerprint,
    }
    extra = out.get("extra_headers") or {}
    out["extra_header_names"] = sorted(extra) if isinstance(extra, Mapping) else []
    return out


def connections(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Several connections, each shaped by connection()."""
    return [connection(value) for value in values or ()]


def model(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A model row with its provider facts and a mock flag that cannot lie.

    The registry derives is_mock from the adapter class rather than a column, so
    a row cannot claim to be real when its provider type says otherwise. This
    keeps the derived value instead of recomputing it from what the caller passed.
    """
    if not value:
        return {}
    out = row(value)
    provider = out.get("provider")
    if isinstance(provider, Mapping):
        out["provider"] = connection(provider)
        out["is_mock"] = bool(provider.get("is_mock", out.get("is_mock", False)))
    else:
        out["is_mock"] = bool(out.get("is_mock", False))
    out["adapter_missing"] = bool(out.get("adapter_missing", False))
    return out


def job(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A job with the metadata the player and the timeline read."""
    if not value:
        return {}
    out = row(value)
    out["status_message"] = str(out.get("status_message") or "")
    out["cancel_requested"] = bool(out.get("cancel_requested_at"))
    estimate = out.get("cost_estimate")
    out["cost_estimate"] = estimate if isinstance(estimate, Mapping) else {}
    error = out.get("provider_error")
    if error is not None and not isinstance(error, Mapping):
        out["provider_error"] = {}
    for name in ("events", "outputs"):
        if isinstance(out.get(name), list):
            out[name] = [row(item) for item in out[name]]
    return out


def asset(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """An asset with the content routes the studio renders from."""
    if not value:
        return {}
    out = row(value)
    identifier = str(out.get("id") or "")
    out["content_url"] = "/api/assets/%s/content" % identifier if identifier else ""
    out["thumbnail_url"] = (
        "/api/assets/%s/thumbnail" % identifier
        if identifier and out.get("thumbnail_key")
        else ""
    )
    return out


def assets(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Several assets, each shaped by asset()."""
    return [asset(value) for value in values or ()]
def _model_ref(repos: Any, workspace_id: str, model_id: str) -> Optional[Dict[str, Any]]:
    """The model block a job's history entry needs, or None if it is gone.

    A job stores `model_id`, and every screen that lists a job wants to name the
    model. The client used to look for a nested `model` block that no route ever
    sent, so job cards and the job detail header showed a blank model. Building it
    here -- from the registry, failing quietly when the model was deleted -- is
    cheaper than teaching four screens to join two payloads.
    """
    if not model_id:
        return None
    try:
        model = repos.models.with_provider(workspace_id, model_id)
    except Exception:  # noqa: BLE001 - a deleted model is a normal case
        return None
    if not isinstance(model, Mapping):
        return None
    return {
        "id": str(model.get("id") or ""),
        "model_key": str(model.get("model_key") or ""),
        "display_name": str(model.get("display_name") or model.get("model_key") or ""),
        "modality": str(model.get("modality") or ""),
    }


def job_with_provider(repos: Any, workspace_id: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    """A job plus the provider facts the client needs to badge a mock.

    is_mock is read from the model registry, which derives it from the adapter
    class rather than a stored column. A job whose connection has since been
    deleted keeps no provider block at all, rather than claiming to be real.
    """
    out = job(value)
    model_id = str(value.get("model_id") or "")
    is_mock = False
    if model_id:
        try:
            model = repos.models.with_provider(workspace_id, model_id)
        except Exception:  # noqa: BLE001 - a stale model must not break a job read
            model = None
        if isinstance(model, Mapping):
            is_mock = bool(model.get("is_mock"))
    provider = None
    connection_id = str(value.get("connection_id") or "")
    if connection_id:
        try:
            found = repos.connections.get(workspace_id, connection_id)
        except Exception:  # noqa: BLE001 - a deleted connection is a normal case
            found = None
        if found:
            provider = connection(found)
            provider["is_mock"] = is_mock
    out["provider"] = provider
    out["is_mock"] = is_mock
    out["model"] = _model_ref(repos, workspace_id, model_id)
    return out
