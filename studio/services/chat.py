"""Conversation over a BYOK endpoint: the chat route's whole engine.

This is deliberately not a generation job. A chat completion is synchronous,
cheap and returns text rather than an asset; putting it through the generation
queue would add a lease, a poll and an asset row to something that takes two
seconds and produces no file. So it is one bounded request, and the response is
exactly what the provider returned.

What is enforced
----------------
* the model must be a conversation model registered in this workspace;
* its connection must be enabled and hold a key;
* the request body is validated before it is sent (roles, count, lengths);
* the endpoint is the connection's own base URL, fetched through the same
  SSRF-guarded egress client every other outbound call uses;
* the provider's error is passed through with its status, not rewritten into a
  success.

The API key is opened for the duration of the call and never returned.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Sequence

from ..discovery import api_root
from ..errors import CONFIGURATION_REQUIRED, VALIDATION_FAILED, ApiError
from ..logging_setup import get_logger
from ..util import json_loads

LOG = get_logger(__name__)

#: A conversation request is bounded: a studio chat is not a batch job.
MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 32_000
MAX_TOKENS = 8_192
CHAT_TIMEOUT_SECONDS = 60.0

ROLES = ("system", "user", "assistant", "tool")

#: Endpoints tried, in order, for the model list. Aggregators differ by exactly
#: this much; trying both is cheaper than asking the user which one it is.
MODEL_PATHS = ("/models", "/v1/models")


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalise_messages(raw: Any) -> List[Dict[str, str]]:
    """Validate a conversation before any of it is sent.

    Refuses an empty conversation, more than ``MAX_MESSAGES``, a role outside the
    vocabulary, and a message longer than the cap -- each by name, because a
    provider's 400 about a field called "messages" is much worse than ours.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ApiError(VALIDATION_FAILED, "A conversation needs at least one message.", field="messages")
    if len(raw) > MAX_MESSAGES:
        raise ApiError(
            VALIDATION_FAILED,
            "A conversation carries at most %d messages." % MAX_MESSAGES,
            field="messages",
        )
    out: List[Dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ApiError(VALIDATION_FAILED, "Message %d is not an object." % (index + 1), field="messages")
        role = _text(item.get("role"), 20).lower()
        if role not in ROLES:
            raise ApiError(
                VALIDATION_FAILED,
                "Message %d has an unknown role '%s'." % (index + 1, role),
                field="messages",
            )
        content = str(item.get("content") or "")
        if not content.strip():
            raise ApiError(VALIDATION_FAILED, "Message %d is empty." % (index + 1), field="messages")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ApiError(
                VALIDATION_FAILED,
                "Message %d is longer than this platform accepts." % (index + 1),
                field="messages",
            )
        out.append({"role": role, "content": content})
    return out


class ChatService:
    """Send one conversation turn to the provider behind a registered model."""

    def __init__(self, repositories: Any, connections: Any, config: Any) -> None:
        self.repos = repositories
        self.connections = connections
        self.config = config

    # -- resolution ---------------------------------------------------------

    def _resolve(self, session: Any, model_id: str) -> tuple:
        model = self.repos.models.with_provider(session.workspace_id, model_id)
        modality = str(model.get("modality") or "")
        if modality not in ("other", "chat"):
            raise ApiError(
                VALIDATION_FAILED,
                "That model is a %s model, not a conversation model." % (modality or "generation"),
                field="model_id",
                hint="Pick a model discovered as 'chat' on the Connections screen.",
            )
        if not model.get("is_enabled"):
            raise ApiError(
                "capability_unsupported",
                "This model is disabled in this workspace.",
                field="model_id",
            )
        connection = self.repos.connections.get(
            session.workspace_id, str(model.get("connection_id") or "")
        )
        if not connection.get("is_enabled"):
            raise ApiError(
                "capability_unsupported",
                "The provider connection behind this model is disabled.",
                field="model_id",
            )
        if not connection.get("credential_ciphertext"):
            raise ApiError(
                CONFIGURATION_REQUIRED,
                "That connection has no key stored, so it cannot be called.",
                field="model_id",
            )
        return model, connection

    # -- the call -----------------------------------------------------------

    def complete(self, session: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Send one conversation and return the provider's answer verbatim."""
        session.require("generation:create")
        model_id = _text(payload.get("model_id"), 200)
        if not model_id:
            raise ApiError(VALIDATION_FAILED, "Choose a conversation model.", field="model_id")
        model, connection = self._resolve(session, model_id)
        messages = normalise_messages(payload.get("messages"))
        max_tokens = payload.get("max_tokens")
        if max_tokens is None or max_tokens == "":
            max_tokens = 1024
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            raise ApiError(VALIDATION_FAILED, "max_tokens must be a whole number.", field="max_tokens") from None
        if not 1 <= max_tokens <= MAX_TOKENS:
            raise ApiError(
                VALIDATION_FAILED,
                "max_tokens must be between 1 and %d." % MAX_TOKENS,
                field="max_tokens",
            )
        temperature = payload.get("temperature", 0.7)
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            raise ApiError(VALIDATION_FAILED, "temperature must be a number.", field="temperature") from None
        if not 0.0 <= temperature <= 2.0:
            raise ApiError(VALIDATION_FAILED, "temperature must be between 0 and 2.", field="temperature")

        body: Dict[str, Any] = {
            "model": str(model.get("model_key") or ""),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        started = time.monotonic()
        result = self._post(connection, "/chat/completions", body, timeout=CHAT_TIMEOUT_SECONDS)
        elapsed = int((time.monotonic() - started) * 1000)

        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ApiError(
                "invalid_response",
                "The provider answered without a message.",
                details={"keys": sorted(result)[:10]},
            )
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
        content = str(message.get("content") or "")
        if not content and isinstance(first.get("text"), str):
            content = str(first["text"])
        return {
            "model_id": model_id,
            "model_key": str(model.get("model_key") or ""),
            "message": {"role": "assistant", "content": content},
            "finish_reason": str(first.get("finish_reason") or ""),
            "usage": result.get("usage") if isinstance(result.get("usage"), Mapping) else {},
            "latency_ms": elapsed,
            "provider": {
                "connection_id": str(connection.get("id") or ""),
                "connection_name": str(connection.get("name") or ""),
                "base_url": str(connection.get("base_url") or ""),
                "is_mock": bool(model.get("is_mock")),
            },
        }

    # -- transport ----------------------------------------------------------

    def _post(self, connection: Mapping[str, Any], path: str, body: Mapping[str, Any], *, timeout: float) -> Dict[str, Any]:
        from ..net import client_for

        context = self.connections.context_for(connection, with_credential=True)
        root = api_root(str(connection.get("base_url") or ""))
        url = root + path
        headers = {"Content-Type": "application/json"}
        credential = context.credential or ""
        scheme = str(connection.get("auth_scheme") or "bearer")
        if credential and scheme == "bearer":
            headers["Authorization"] = "Bearer %s" % credential
        elif credential and scheme == "header":
            headers[str(connection.get("auth_header_name") or "Authorization")] = credential
        elif credential and scheme == "query":
            from urllib.parse import quote

            url += ("&" if "?" in url else "?") + "key=" + quote(credential, safe="")
        for name, value in (context.extra_headers or {}).items():
            headers[str(name)] = str(value)

        client = client_for(self.config)
        try:
            response = client.request(
                "POST",
                url,
                headers=headers,
                json_body=dict(body),
                accept="application/json",
                max_bytes=4 * 1024 * 1024,
                timeout_seconds=timeout,
                read_timeout=timeout,
            )
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001 - a transport failure is a 502
            LOG.warning("chat transport failed", extra={"reason": type(error).__name__})
            raise ApiError(
                "network_error",
                "The provider could not be reached.",
                details={"reason": type(error).__name__},
                hint="Check the base URL and that the host is reachable from this deployment.",
            ) from None
        payload = json_loads(response.text() or "{}", {})
        if not response.ok:
            detail = ""
            if isinstance(payload, Mapping):
                error = payload.get("error")
                if isinstance(error, Mapping):
                    detail = str(error.get("message") or "")
                else:
                    detail = str(payload.get("message") or "")
            raise ApiError(
                "provider_error",
                "The provider refused the request.",
                provider_message=detail or ("HTTP %d" % response.status),
                details={"status": response.status},
            )
        if not isinstance(payload, Mapping):
            raise ApiError("invalid_response", "The provider answered with something that is not an object.")
        return dict(payload)

    # -- onboarding probe ---------------------------------------------------

    def probe(self, base_url: str, api_key: str, model_key: str, *, timeout: float = 8.0) -> Dict[str, Any]:
        """One honest availability check: send a one-token turn and report it.

        This is the only probe the onboarding flow runs by default. It is cheap
        (one token out), bounded, and reports the provider's own status rather
        than inferring availability from the model being listed.
        """
        from ..net import client_for

        root = api_root(base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer %s" % api_key
        body = {
            "model": model_key,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        started = time.monotonic()
        try:
            response = client_for(self.config).request(
                "POST",
                root + "/chat/completions",
                headers=headers,
                json_body=body,
                accept="application/json",
                max_bytes=256 * 1024,
                timeout_seconds=timeout,
                read_timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001 - a failed probe is data, not a crash
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": type(error).__name__,
            }
        elapsed = int((time.monotonic() - started) * 1000)
        payload = json_loads(response.text() or "{}", {})
        if not response.ok:
            detail = ""
            if isinstance(payload, Mapping):
                error = payload.get("error")
                detail = str(error.get("message") or "") if isinstance(error, Mapping) else ""
            return {
                "ok": False,
                "latency_ms": elapsed,
                "status": response.status,
                "error": detail or ("HTTP %d" % response.status),
            }
        return {"ok": True, "latency_ms": elapsed, "status": response.status}
