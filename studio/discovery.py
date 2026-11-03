"""What an aggregator's model list means: classification, not guesswork.

The one-key flow starts with `GET /v1/models`, which returns ids and almost
nothing else. This module turns those ids into a **proposal**: which family each
model belongs to, whether this build can drive it, and why. It is pure, so the
classification can be tested without a network.

Two rules keep the proposal honest
----------------------------------
1. **An explicit capability wins.** When the provider states a modality or an
   architecture, that is used and the name is ignored. Heuristics only fill the
   gap the provider left.
2. **Unsupported is a result.** A video or audio model is still reported -- the
   user asked what the endpoint has -- but it is marked as not drivable by this
   build rather than registered as a button that cannot work.

Nothing here calls a provider, and nothing is registered: the proposal is a
document the user reads before anything is written.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: The families this build can name.
CHAT = "chat"
IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
EMBEDDING = "embedding"
RERANK = "rerank"
MODERATION = "moderation"
UNKNOWN = "unknown"

FAMILIES: Tuple[str, ...] = (CHAT, IMAGE, VIDEO, AUDIO, EMBEDDING, RERANK, MODERATION, UNKNOWN)

#: The family this build can drive end to end. Chat is routed through the
#: conversation service; image is routed through the OpenAI-images adapter.
DRIVABLE: Tuple[str, ...] = (CHAT, IMAGE)

#: The model this workspace wants for conversation when it is present.
DEFAULT_CHAT_MODEL = "deepseek-v4.1-flash"


def api_root(base_url: str) -> str:
    """The OpenAI-compatible root of a provider URL.

    An aggregator is published as either `https://host` or `https://host/v1`.
    Both mean the same API; appending `/v1` to the second would produce
    `/v1/v1/models`, which is the most common way a one-field setup fails.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return base + "/v1"


#: Ordered patterns. A model is classified by the first family whose pattern
#: matches, so the specific families come before the general chat patterns:
#: `gpt-image-2` is an image model, not a chat model with "gpt" in its name.
_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    (EMBEDDING, re.compile(r"(embed|embedding|bge-|e5-|text-embedding)", re.I)),
    (RERANK, re.compile(r"(rerank|re-rank)", re.I)),
    (MODERATION, re.compile(r"(moderation|omni-moderation)", re.I)),
    (AUDIO, re.compile(r"(^|[-_/])(tts|asr|stt|whisper|speech|voice|audio|music|suno|eleven)", re.I)),
    (
        VIDEO,
        re.compile(
            r"(seedance|kling|wan[0-9.]*-?video|wanx|veo[0-9-]*|sora|runway|pika|luma|"
            r"minimax-h[0-9]|muse-video|grok-imagine-1|hailuo|vidu|mochi|ltx|"
            r"gemini-omni|video-generation|text-to-video|image-to-video)",
            re.I,
        ),
    ),
    (
        IMAGE,
        re.compile(
            r"(gpt-image|dall-?e|imagen|flux|seedream|stable-diffusion|sdxl|sd3|"
            r"midjourney|nano-banana|muse-image|grok-imagine-2|qwen-image|"
            r"image-preview|image-generation|text-to-image|image-to-image|"
            r"(^|[-_/])image([-_/]|$))",
            re.I,
        ),
    ),
    (
        CHAT,
        re.compile(
            r"(gpt-|claude|gemini|deepseek|qwen|mimo|minimax-m|grok-[0-9]|hy3|"
            r"llama|mistral|mixtral|kimi|glm|command-r|o[1-9](-|$)|chat|instruct)",
            re.I,
        ),
    ),
)


def _declared(entry: Mapping[str, Any]) -> Optional[str]:
    """The family the provider itself states, if any.

    Handles the shapes aggregators actually return: a plain `type`, a `modality`
    string, or an OpenRouter-style `architecture.modality` such as
    ``"text->image"``.
    """
    for key in ("family", "type", "modality", "capability"):
        value = str(entry.get(key) or "").strip().lower()
        if value in FAMILIES:
            return value
        if value.startswith("text->"):
            target = value.split("->", 1)[1]
            if target in FAMILIES:
                return target
            if target == "text":
                return CHAT
    architecture = entry.get("architecture")
    if isinstance(architecture, Mapping):
        return _declared({"modality": architecture.get("modality")})
    return None


def classify(model_id: str, entry: Optional[Mapping[str, Any]] = None) -> str:
    """The family of one model: declared if stated, otherwise by name.

    A declared family always wins. A model that names both an image and a video
    word (`grok-imagine`) is resolved by the ordered patterns, and an id that
    matches nothing is `unknown` rather than being assumed to be chat.
    """
    declared = _declared(entry or {})
    if declared:
        return declared
    for family, pattern in _PATTERNS:
        if pattern.search(model_id or ""):
            return family
    return UNKNOWN


def display_name(entry: Mapping[str, Any], model_id: str) -> str:
    for key in ("name", "display_name", "label"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value[:200]
    return model_id[:200]


def _entry_id(entry: Mapping[str, Any]) -> str:
    for key in ("id", "model", "model_key", "name"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def proposal(
    entries: Sequence[Mapping[str, Any]],
    *,
    default_chat: str = DEFAULT_CHAT_MODEL,
) -> Dict[str, Any]:
    """Turn a raw `/v1/models` list into the plan the register step writes.

    Each model carries its family, whether this build can drive it, the modality
    and modes it would be registered with, and a one-line reason. An id that
    appears twice is collapsed (aggregators frequently list aliases).
    """
    seen: set = set()
    models: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        model_id = _entry_id(entry)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        family = classify(model_id, entry)
        drivable = family in DRIVABLE
        item: Dict[str, Any] = {
            "model_key": model_id,
            "display_name": display_name(entry, model_id),
            "family": family,
            "drivable": drivable,
            "modality": IMAGE if family == IMAGE else "other",
            "modes": ["text_to_image", "image_to_image"] if family == IMAGE else [],
            "reason": "",
        }
        if family == CHAT:
            item["reason"] = "Conversation model; driven through the chat route."
        elif family == IMAGE:
            item["reason"] = "Image model; driven through the OpenAI-images adapter."
        elif family in (VIDEO, AUDIO):
            item["reason"] = (
                "Discovered, but this build has no adapter for %s models on a generic "
                "aggregator. It is listed, not registered as a button that cannot work."
                % family
            )
        elif family in (EMBEDDING, RERANK, MODERATION):
            item["reason"] = "Not a generation model; nothing in this studio consumes it."
        else:
            item["reason"] = "Could not be classified from its id; register it by hand if you know what it is."
        models.append(item)

    chat_models = [item["model_key"] for item in models if item["family"] == CHAT and item["drivable"]]
    chosen = next((key for key in chat_models if key == default_chat), None)
    if chosen is None:
        chosen = next((key for key in chat_models if default_chat in key), None)
    if chosen is None and chat_models:
        chosen = chat_models[0]

    counts = {family: 0 for family in FAMILIES}
    for item in models:
        counts[item["family"]] = counts.get(item["family"], 0) + 1
    return {
        "models": models,
        "counts": counts,
        "default_chat": chosen or "",
        "default_chat_requested": default_chat,
        "warnings": []
        if chosen == default_chat
        else [
            "The endpoint does not list %s, so %s is proposed for conversation."
            % (default_chat, chosen or "nothing")
        ]
        if chat_models
        else ["The endpoint lists no conversation model."],
        "notes": {
            "classify": "A declared capability wins; otherwise the id is matched against a family table.",
            "register": "Only chat and image models are registered as drivable. The rest are reported.",
        },
    }
