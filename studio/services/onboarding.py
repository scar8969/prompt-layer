"""One key, one URL: discover what a provider offers, then register what works.

The flow has two halves and one rule.

* **Discover** fetches the endpoint's own model list, classifies it (see
  :mod:`studio.discovery`) and -- by default -- sends one one-token turn to every
  conversation model, so "available" means the provider answered, not that the id
  appeared in a list.
* **Register** creates one connection per protocol with the sealed key and records
  the models the user confirmed. Conversation models are registered as `other`
  (they are driven by the chat route, not an adapter); image models as `image`
  on the OpenAI-images adapter; video models as `video` on the OpenAI-videos
  adapter. One key and one URL therefore become one, two or three connection
  records -- one per wire protocol, which is what a connection means here.
* **The rule**: a model this build cannot drive is reported, never registered as
  a button that cannot work. Audio and the non-generation families from a
  generic aggregator have no adapter here, and the response says so.

Chat probes are cheap but not free. They are capped, bounded and labelled; image
and video probes are opt-in because one image call can cost more than every chat
probe on the page combined.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Mapping, Sequence

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

#: The adapter type stored for each family. Only types this database can store
#: are used: images have a dedicated adapter, while chat and video are
#: declarative connections carrying the mapping that is true for that protocol.
#: A connection stores one protocol, so one key becomes one record per family
#: (chat rides on a media connection when there is one, because chat reads the
#: base URL and key itself and needs no adapter).
ADAPTER_BY_FAMILY: Dict[str, str] = {
    discovery.CHAT: "declarative_http",
    discovery.IMAGE: "openai_images",
    discovery.VIDEO: "declarative_http",
}

#: Short suffixes for the connections created from one key.
CONNECTION_SUFFIX: Dict[str, str] = {
    discovery.CHAT: "chat",
    discovery.IMAGE: "images",
    discovery.VIDEO: "video",
}

#: The defaults each family is registered with. Video is deliberately narrow:
#: the OpenAI-videos mapping expresses text to video and nothing else yet.
FAMILY_MODES: Dict[str, Sequence[str]] = {
    discovery.IMAGE: ("text_to_image", "image_to_image"),
    discovery.VIDEO: ("text_to_video",),
}


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

        groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in chosen:
            groups.setdefault(item["family"], []).append(item)

        # Chat does not need an adapter: the chat route reads the connection's
        # base URL and key itself. So when a media protocol exists, its models
        # ride on that connection rather than opening a second one with the same
        # URL and key. A chat-only registration gets its own declarative
        # connection carrying the one mapping that is true: one POST, the answer
        # already in the response.
        media_families = [f for f in (discovery.IMAGE, discovery.VIDEO) if f in groups]
        order = media_families + ([discovery.CHAT] if discovery.CHAT in groups else [])
        chat_ride = bool(media_families) and discovery.CHAT in groups

        names = _connection_names(name, order, collapse_chat=chat_ride)
        created: List[Dict[str, Any]] = []
        registered: List[Dict[str, Any]] = []
        models_by_connection: Dict[str, List[Dict[str, Any]]] = {family: [] for family in order}

        for family in order:
            if family == discovery.CHAT and chat_ride:
                continue
            adapter_type = ADAPTER_BY_FAMILY[family]
            create_payload: Dict[str, Any] = {
                "name": names[family],
                "adapter_type": adapter_type,
                # The fixed image and video mappings carry /v1 in their paths, so
                # their base is the host root; a pasted .../v1 would double it.
                "base_url": base_url if family == discovery.CHAT else discovery.host_root(base_url),
                "auth_scheme": "bearer",
                "credential": api_key,
                "allowed_hosts": clean_allowed_hosts(payload.get("allowed_hosts")),
                "models": [],
            }
            if family == discovery.CHAT:
                create_payload["mapping"] = {
                    "structure": "sync",
                    "submit": {
                        "path": discovery.api_root(base_url) + "/chat/completions",
                        "prompt_field": "messages",
                        "model_field": "model",
                    },
                    "response": {"type": "json", "outputs_path": "choices.0.message.content"},
                }
            elif family == discovery.VIDEO:
                # The OpenAI-videos shape, declared as data and used through the
                # generic declarative adapter. A dedicated provider type would
                # need a new value in the database's adapter check, which this
                # build does not have; the mapping is the same thing without a
                # schema change.
                from ..adapters.declarative import OPENAI_VIDEOS_SPEC

                create_payload["mapping"] = OPENAI_VIDEOS_SPEC
            connection = self.connections.create(session, create_payload)["connection"]
            created.append(connection)
            connection_id = str(connection["id"])
            models_by_connection[family] = [
                self.connections.add_model(session, connection_id, _model_payload(item))
                for item in groups[family]
            ]
            registered.extend(models_by_connection[family])

        if chat_ride:
            host_family = media_families[0]
            host_connection = created[media_families.index(host_family)]
            models_by_connection[discovery.CHAT] = [
                self.connections.add_model(
                    session, str(host_connection["id"]), _model_payload(item)
                )
                for item in groups[discovery.CHAT]
            ]
            registered.extend(models_by_connection[discovery.CHAT])

        primary = created[0]
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
            target_id=str(primary["id"]),
            request_id="",
            ip_hash="",
            detail={
                "models": len(registered),
                "connections": len(created),
                "families": order,
            },
        )
        return {
            "connection": primary,
            "connections": created,
            "models": registered,
            "models_by_family": models_by_connection,
            "default_chat_model_id": default_model_id,
            "default_chat": default_chat,
            "notes": {
                "adapter": ", ".join(str(item.get("adapter_type") or "") for item in created),
                "split": (
                    "One key became %d connections because a connection is one wire "
                    "protocol: %s." % (len(created), ", ".join(order))
                    if len(created) > 1
                    else "One connection drives every model registered here."
                ),
                "chat": (
                    "Conversation models share the %s connection; chat does not use "
                    "its generation mapping." % media_families[0]
                    if chat_ride
                    else "Conversation has its own declarative connection."
                ),
                "spend": "Registering costs nothing. Chat is billed per token; images per request; video per generation.",
            },
        }


def _connection_names(base: str, families: Sequence[str], *, collapse_chat: bool = False) -> Dict[str, str]:
    """A name per family: bare when there is only one, suffixed when split.

    Chat riding on a media connection does not count as a split, because it adds
    no protocol record of its own.
    """
    shown = [family for family in families if not (collapse_chat and family == discovery.CHAT)]
    if len(shown) <= 1:
        return {family: base for family in families}
    return {
        family: "%s (%s)" % (base, CONNECTION_SUFFIX.get(family, family)) for family in families
    }


def _model_payload(item: Mapping[str, Any]) -> Dict[str, Any]:
    """What one discovered model becomes in the registry, per family.

    The modes are the narrowest honest set: an image endpoint that follows the
    OpenAI images protocol can edit from a reference; the videos mapping in this
    build expresses text to video only. Nothing claims a mode that has not been
    mapped.
    """
    model_key = str(item["model_key"])
    display_name = str(item.get("display_name") or model_key)
    family = str(item["family"])
    if family == discovery.CHAT:
        return {
            "model_key": model_key,
            "display_name": display_name,
            "modality": "other",
            "modes": [],
            "source": "discovered",
        }
    if family == discovery.VIDEO:
        return {
            "model_key": model_key,
            "display_name": display_name,
            "modality": "video",
            "modes": list(FAMILY_MODES[family]),
            "source": "discovered",
        }
    return {
        "model_key": model_key,
        "display_name": display_name,
        "modality": "image",
        "modes": list(FAMILY_MODES[family]),
        "capabilities": {"max_outputs": 4},
        "source": "discovered",
    }
