"""One key, one URL: discover what a provider offers, then register what works.

The flow has two halves and one rule.

* **Discover** fetches the endpoint's own model list, classifies it (see
  :mod:`studio.discovery`) and -- by default -- sends one one-token turn to every
  conversation model, so "available" means the provider answered, not that the id
  appeared in a list.
* **Register** creates one connection with the sealed key and records the models
  the user confirmed. Conversation models are registered as `other`; image models
  as `image` with the modes the OpenAI-images protocol always has.
* **The rule**: a model this build cannot drive is reported, never registered as
  a button that cannot work. Video and audio models from a generic aggregator need
  a wire format this repository does not have, and the response says so.

Chat probes are cheap but not free. They are capped, bounded and labelled; image
and video probes are opt-in because one image call can cost more than every chat
probe on the page combined.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .. import discovery
from ..errors import VALIDATION_FAILED, ApiError
from ..logging_setup import get_logger
from ..util import json_loads
from .connections import clean_allowed_hosts

LOG = get_logger(__name__)

#: The deepest list a single discovery may fetch. A provider with more than this
#: is paginated or is not a model list; either way, reading all of it is not what
#: the user asked for.
MAX_DISCOVERED = 500

#: Conversation probes: how many, how fast, and how long each may take.
MAX_CHAT_PROBES = 24
PROBE_WORKERS = 6
PROBE_TIMEOUT_SECONDS = 8.0

MODEL_LIST_PATHS = ("/models", "/v1/models")


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


class OnboardingService:
    """Turn a base URL and a key into a reviewed registry."""

    def __init__(self, repositories: Any, connections: Any, chat: Any, config: Any) -> None:
        self.repos = repositories
        self.connections = connections
        self.chat = chat
        self.config = config

    # -- discovery ----------------------------------------------------------

    def _fetch_models(self, base_url: str, api_key: str) -> List[Dict[str, Any]]:
        """The provider's own list, read through the egress policy.

        Both `/models` and `/v1/models` are tried because aggregators publish one
        or the other under the same base URL. A non-2xx answer is a refusal with
        the provider's status, not an empty list that looks like "no models".
        """
        from ..net import client_for

        root = discovery.api_root(base_url)
        candidates = [root + "/models", base_url.rstrip("/") + "/models"]
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer %s" % api_key
        client = client_for(self.config)
        last_error = ""
        for url in dict.fromkeys(candidates):
            try:
                response = client.request(
                    "GET",
                    url,
                    headers=headers,
                    accept="application/json",
                    max_bytes=2 * 1024 * 1024,
                    timeout_seconds=15.0,
                    read_timeout=15.0,
                )
            except ApiError as error:
                last_error = error.message
                continue
            except Exception as error:  # noqa: BLE001 - a failed probe is data
                last_error = type(error).__name__
                continue
            if not response.ok:
                last_error = "HTTP %d from %s" % (response.status, url)
                if response.status in (401, 403):
                    raise ApiError(
                        VALIDATION_FAILED,
                        "The provider refused the key.",
                        field="api_key",
                        details={"status": response.status},
                        hint="Check that the key is complete and belongs to this endpoint.",
                    )
                continue
            payload = json_loads(response.text() or "", None)
            items: Any = payload
            if isinstance(payload, Mapping):
                items = payload.get("data") or payload.get("models") or payload.get("result")
            if isinstance(items, list):
                entries = [item for item in items if isinstance(item, Mapping)][:MAX_DISCOVERED]
                if entries:
                    return entries
            last_error = "The answer contained no model list."
        raise ApiError(
            VALIDATION_FAILED,
            "The endpoint did not return a model list.",
            field="base_url",
            details={"last_error": last_error},
            hint="Use the API base URL (often ending in /v1), not the website address.",
        )

    def discover(self, session: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Read the catalogue, classify it, and probe the conversation models.

        Nothing is written. The response is the document the user confirms.
        """
        session.require("connection:manage")
        name = _text(payload.get("name"), 120) or "Discovered provider"
        base_url = _text(payload.get("base_url"), 2000)
        api_key = str(payload.get("api_key") or "")
        if not base_url:
            raise ApiError(VALIDATION_FAILED, "The API base URL is required.", field="base_url")
        if not api_key and not payload.get("allow_no_key"):
            raise ApiError(
                VALIDATION_FAILED,
                "An API key is required.",
                field="api_key",
                hint="A public endpoint can be read by setting allow_no_key.",
            )
        checked = self.connections.check_base_url(base_url, clean_allowed_hosts(payload.get("allowed_hosts")))
        entries = self._fetch_models(checked, api_key)
        plan = discovery.proposal(entries)
        probe_chat = payload.get("probe_chat", True) is not False
        if probe_chat:
            self._probe_chat(plan, checked, api_key)
        plan["provider"] = {
            "name": name,
            "base_url": checked,
            "model_list_path": discovery.api_root(checked) + "/models",
        }
        plan["notes"]["probe"] = (
            "A conversation model is marked available only when the endpoint answered a "
            "one-token turn. Image and video models are not probed: one image call can "
            "cost more than every chat probe combined."
        )
        return plan

    def _probe_chat(self, plan: Dict[str, Any], base_url: str, api_key: str) -> None:
        targets = [
            item for item in plan["models"] if item["family"] == discovery.CHAT and item["drivable"]
        ][:MAX_CHAT_PROBES]
        if not targets:
            return

        def run(item: Mapping[str, Any]) -> Dict[str, Any]:
            return self.chat.probe(base_url, api_key, str(item["model_key"]), timeout=PROBE_TIMEOUT_SECONDS)

        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
            results = list(pool.map(run, targets))
        for item, result in zip(targets, results):
            item["probe"] = result
            item["available"] = bool(result.get("ok"))
        probed = sum(1 for item in targets if item.get("available"))
        plan["probe_summary"] = {
            "probed": len(targets),
            "available": probed,
            "failed": len(targets) - probed,
            "max_probes": MAX_CHAT_PROBES,
        }

    # -- registration -------------------------------------------------------

    def register(self, session: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Create the connection and the selected models in one reviewed step.

        The body repeats the provider facts and the chosen model list; the server
        does not trust a client to have run discovery first, but it does not fetch
        again either -- the user saw a list, and this is that list.
        """
        session.require("connection:manage")
        name = _text(payload.get("name"), 120) or "Discovered provider"
        base_url = _text(payload.get("base_url"), 2000)
        api_key = str(payload.get("api_key") or "")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ApiError(
                VALIDATION_FAILED,
                "Choose at least one model to register.",
                field="models",
            )
        default_chat = _text(payload.get("default_chat"), 200) or discovery.DEFAULT_CHAT_MODEL

        chosen: List[Dict[str, Any]] = []
        for item in raw_models:
            if not isinstance(item, Mapping):
                continue
            model_key = _text(item.get("model_key"), 300)
            if not model_key:
                continue
            family = _text(item.get("family"), 20) or discovery.classify(model_key)
            if family not in discovery.DRIVABLE:
                raise ApiError(
                    VALIDATION_FAILED,
                    "%s is a %s model, which this build cannot drive on a generic aggregator."
                    % (model_key, family),
                    field="models",
                )
            chosen.append({"model_key": model_key, "family": family, "display_name": _text(item.get("display_name"), 200)})
        if not chosen:
            raise ApiError(VALIDATION_FAILED, "Choose at least one model to register.", field="models")

        adapter_type = (
            "openai_images" if any(item["family"] == discovery.IMAGE for item in chosen) else "declarative_http"
        )
        create_payload: Dict[str, Any] = {
            "name": name,
            "adapter_type": adapter_type,
            "base_url": base_url,
            "auth_scheme": "bearer",
            "credential": api_key,
            "allowed_hosts": clean_allowed_hosts(payload.get("allowed_hosts")),
            "models": [],
        }
        if adapter_type == "declarative_http":
            # A chat-only connection has no generation mapping to describe, but a
            # declarative provider without one is refused at save time -- correctly,
            # because a queued provider that cannot say where to submit is a button
            # that cannot work. The conversation protocol is the one mapping that is
            # true here: one POST, the answer already in the response.
            create_payload["mapping"] = {
                "structure": "sync",
                "submit": {
                    "path": discovery.api_root(base_url) + "/chat/completions",
                    "prompt_field": "messages",
                    "model_field": "model",
                },
                "response": {"type": "json", "outputs_path": "choices.0.message.content"},
            }
        created = self.connections.create(session, create_payload)
        connection = created["connection"]
        registered: List[Dict[str, Any]] = []
        for item in chosen:
            if item["family"] == discovery.CHAT:
                model_payload = {
                    "model_key": item["model_key"],
                    "display_name": item["display_name"] or item["model_key"],
                    "modality": "other",
                    "modes": [],
                    "source": "discovered",
                }
            else:
                model_payload = {
                    "model_key": item["model_key"],
                    "display_name": item["display_name"] or item["model_key"],
                    "modality": "image",
                    "modes": ["text_to_image", "image_to_image"],
                    "capabilities": {"max_outputs": 4},
                    "source": "discovered",
                }
            registered.append(
                self.connections.add_model(session, str(connection["id"]), model_payload)
            )

        default_model_id = ""
        chat_models = [
            model
            for model in registered
            if str(model.get("modality") or "") == "other"
        ]
        if chat_models:
            preferred = next(
                (model for model in chat_models if str(model.get("model_key") or "") == default_chat), None
            ) or next(
                (model for model in chat_models if default_chat in str(model.get("model_key") or "")), None
            ) or chat_models[0]
            self.repos.models.set_default(session.workspace_id, str(preferred["id"]))
            default_model_id = str(preferred["id"])

        self.repos.audit.record(
            session.workspace_id,
            session.user_id,
            "connection.onboarded",
            target_type="connection",
            target_id=str(connection["id"]),
            request_id="",
            ip_hash="",
            detail={"models": len(registered), "adapter_type": adapter_type},
        )
        return {
            "connection": connection,
            "models": registered,
            "default_chat_model_id": default_model_id,
            "default_chat": default_chat,
            "notes": {
                "adapter": (
                    "openai_images" if adapter_type == "openai_images" else "declarative_http"
                ),
                "spend": "Registering costs nothing. Chat is billed per token; images per request.",
            },
        }
