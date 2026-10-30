"""The prompt layer, inside the studio instead of beside it.

This route group is the merge point between the two halves of the repository.
The layer dataset (`promptlayer/layers.json`) and the assembly logic
(`promptlayer/assembler.py`) used to be served by a separate static page with its
own JSON functions. They are now part of the same application: one session, one
workspace, one route table, one client.

Three things are exposed:

* ``GET /api/prompt/models`` — every model the dataset has facts for, with the
  count of layers that are active by default. This is a catalogue, not a
  capability claim: a model absent here is not "supported", it is merely
  undocumented, and the preflight response says so.
* ``GET /api/prompt/models/{slug}`` — one model, its layers, its parameter
  defaults, and the sources those facts came from.
* ``POST /api/prompt/preflight`` — the assembled upstream payload for a
  (model, prompt, options) triple, with each layer's active state, the knob that
  turns it off, and the resolved parameters with their source.
* ``GET /api/prompt/catalog`` — the endpoints this platform can drive with a
  user's own key, joined to the layer facts above. One list, so adding a model
  and knowing what that model does to a prompt are the same screen.

Nothing here touches the network. The dataset is read from disk, and an unknown
model returns the generic layers with ``unknown_model: true`` rather than a
guessed default -- the same honesty rule the standalone tool kept.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..errors import NOT_FOUND, VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response

#: Longest prompt this route accepts. The dataset adds a bounded number of
#: characters around it, so this is a request-shape limit, not a model limit.
MAX_PROMPT = 20_000
MAX_SYSTEM_MESSAGE = 8_000
MAX_IMAGES = 8


def _prompt_from(body: Dict[str, Any]) -> str:
    """The prompt, refused by name when it is missing or unusable."""
    raw = body.get("prompt")
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(VALIDATION_FAILED, "A prompt is required.", field="prompt")
    if len(raw) > MAX_PROMPT:
        raise ApiError(
            VALIDATION_FAILED,
            "The prompt is longer than %d characters." % MAX_PROMPT,
            field="prompt",
        )
    return raw


def _options_from(body: Dict[str, Any]) -> Dict[str, Any]:
    """Parameter overrides. Values are passed through untouched, as the dataset
    compares them against documented defaults by equality."""
    raw = body.get("options")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ApiError(
            VALIDATION_FAILED, "Field 'options' must be an object.", field="options"
        )
    return dict(raw)


def _catalog_entry(facts: Any, layer_models: Dict[str, str], detail: Any) -> Dict[str, Any]:
    """One endpoint: what the platform can drive, and what it will do to a prompt.

    Every field comes from a document in this repository. `prompt_model` is the
    join to the layer dataset and is `None`, not `{}`, when no document describes
    this endpoint -- the client shows that absence rather than an empty panel that
    reads like a clean bill of health.
    """
    slug = layer_models.get(facts.key)
    described = detail(slug) if slug else None
    layers: List[Dict[str, Any]] = []
    if described:
        for layer in described.get("layers") or ():
            layers.append(
                {
                    "id": layer.get("id"),
                    "kind": layer.get("kind"),
                    "active": bool(layer.get("active", True)),
                    "detail": layer.get("detail"),
                    "disable_with": layer.get("disable_with"),
                    "source": layer.get("source"),
                }
            )
    return {
        "endpoint": facts.key,
        "display": facts.display_name,
        "modality": facts.modality,
        "modes": list(facts.modes),
        "aspect_ratios": list(facts.aspect_ratios),
        "resolutions": list(facts.resolutions),
        "durations_seconds": list(facts.durations_seconds),
        "max_outputs": facts.max_outputs,
        "supports_negative_prompt": facts.supports_negative_prompt,
        "supports_seed": facts.supports_seed,
        "restrictions": list(facts.restrictions),
        "notes": list(facts.notes),
        "prompt_model": (
            {
                "slug": described["slug"],
                "display": described["display"],
                "kind": described["kind"],
                "layer_count": len(layers),
                "active_layer_count": sum(1 for item in layers if item["active"]),
                "params": {
                    name: dict(spec or {})
                    for name, spec in (described.get("params") or {}).items()
                },
                "layers": layers,
            }
            if described
            else None
        ),
    }


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def models(request: RequestContext) -> Response:
        """Every documented model, with its dataset version attached.

        The version travels with the list because a maintainer reading a saved
        report needs to know which dataset produced it.
        """
        from promptlayer import dataset_version, model_list

        rows = model_list()
        modality = request.arg("kind") or ""
        if modality:
            rows = [row for row in rows if row["kind"] == modality]
        return Response.json({"models": rows, "dataset_version": dataset_version()})

    def model(request: RequestContext) -> Response:
        """One model's layers and parameter defaults, with sources."""
        from promptlayer import model_detail

        detail = model_detail(request.params["id"])
        if not detail:
            raise ApiError(
                NOT_FOUND,
                "The layer dataset has no facts for '%s'." % request.params["id"],
                hint="GET /api/prompt/models lists every model it does describe.",
            )
        return Response.json({"model": detail})

    def catalog(request: RequestContext) -> Response:
        """The models this platform can drive on a user's own key.

        Joined to the layer dataset so the picker can state, at the point of
        selection, what the provider will do to the prompt. `modality` filters to
        image or video; anything else returns the whole catalogue.
        """
        from ..adapters.fal_endpoints import ENDPOINTS

        from promptlayer import dataset_version, endpoint_index, model_detail

        mapping = endpoint_index()
        wanted = request.arg("modality") or ""
        rows = [
            _catalog_entry(facts, mapping, model_detail)
            for facts in ENDPOINTS
            if not wanted or facts.modality == wanted
        ]
        described = sum(1 for row in rows if row["prompt_model"])
        return Response.json(
            {
                "endpoints": rows,
                "documented": described,
                "undocumented": len(rows) - described,
                "dataset_version": dataset_version(),
                "source": "studio/adapters/fal_endpoints.py + promptlayer/layers.json",
            }
        )

    def preflight(request: RequestContext) -> Response:
        """What the provider receives, and every layer that touched it."""
        from promptlayer import build_preflight, model_list

        body = request.json()
        prompt = _prompt_from(body)
        slug = body.get("model")
        if not isinstance(slug, str) or not slug.strip():
            raise ApiError(VALIDATION_FAILED, "Field 'model' is required.", field="model")
        image_count = body.get("image_count") or 0
        try:
            image_count = int(image_count)
        except (TypeError, ValueError):
            raise ApiError(
                VALIDATION_FAILED, "Field 'image_count' must be an integer.", field="image_count"
            ) from None
        if image_count < 0 or image_count > MAX_IMAGES:
            raise ApiError(
                VALIDATION_FAILED,
                "Field 'image_count' must be between 0 and %d." % MAX_IMAGES,
                field="image_count",
            )
        system_message = body.get("system_message")
        if system_message is not None and not isinstance(system_message, str):
            raise ApiError(
                VALIDATION_FAILED, "Field 'system_message' must be a string.", field="system_message"
            )
        if isinstance(system_message, str) and len(system_message) > MAX_SYSTEM_MESSAGE:
            raise ApiError(
                VALIDATION_FAILED,
                "Field 'system_message' is longer than %d characters." % MAX_SYSTEM_MESSAGE,
                field="system_message",
            )
        # An unknown slug is not an error: the response carries the generic layers
        # and says the model is undocumented. Refusing here would look like the
        # route is broken rather than like the dataset is silent.
        known = {item["slug"] for item in model_list()}
        result = build_preflight(
            model_slug=slug,
            prompt=prompt,
            options=_options_from(body),
            system_message=system_message,
            image_count=image_count,
        )
        result["known_model"] = slug in known
        return Response.json({"preflight": result})

    def modes(request: RequestContext) -> Response:
        """The generation-mode vocabulary, from the backend that validates it.

        The connection wizard used to hardcode a list of mode names, three of
        which (`generate`, `edit`, `transform`) are not in the vocabulary the
        service accepts -- so registering a model through the UI failed with
        "not a generation mode" no matter what the user ticked. Serving the
        vocabulary from the module that enforces it removes that class of drift.
        """
        from ..adapters.base import IMAGE_MODES, MODE_LABELS, VIDEO_MODES

        everything = tuple(IMAGE_MODES) + tuple(VIDEO_MODES)
        return Response.json(
            {
                "image": list(IMAGE_MODES),
                "video": list(VIDEO_MODES),
                "labels": {mode: MODE_LABELS.get(mode, mode) for mode in everything},
            }
        )

    router.add("GET", "/api/prompt/models", models)
    router.add("GET", "/api/prompt/modes", modes)
    router.add("GET", "/api/prompt/models/{id}", model)
    router.add("GET", "/api/prompt/catalog", catalog)
    router.add("POST", "/api/prompt/preflight", preflight)
