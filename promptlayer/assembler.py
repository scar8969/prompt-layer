"""tokN Prompt Layer — preflight assembly and compile logic.

Pure functions only: no I/O, no network, no framework. The HTTP service in
server.py is a thin wrapper, and tests import this module directly.

Two user-facing operations:

  build_preflight(model, prompt, options) ->
      Exactly what the upstream will receive for a given model + prompt, which
      layers touch the prompt, what to set to turn each one off, and a source
      for every claim.

  build_compile_request(prompt, style, model) ->
      The verbatim system prompt of an open-source prompt expander (Wan,
      HunyuanVideo, Kandinsky) plus the request body that would be sent.

Design rule: never invent a layer or a default. If a model is unknown, say so
(unknown_model: true) and return the generic layers only. Every layer entry
carries `source` + `evidence` so the UI can show where the claim comes from.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
_LAYERS: Optional[Dict[str, Any]] = None

# Rough token estimate for the image-panel. Deliberately crude and labelled as
# an estimate in the API response: we will not dress up a guess as a count.
CHARS_PER_TOKEN = 4


def _load() -> Dict[str, Any]:
    global _LAYERS
    if _LAYERS is None:
        with open(os.path.join(HERE, "layers.json"), encoding="utf-8") as fh:
            _LAYERS = json.load(fh)
    return _LAYERS


def reload_layers() -> Dict[str, Any]:
    """Force a re-read of layers.json (used by tests and by the ops endpoint)."""
    global _LAYERS
    _LAYERS = None
    return _load()


def model_slugs() -> List[str]:
    return sorted(_load()["models"].keys())


def dataset_version() -> str:
    return _load()["version"]


def expander_styles() -> List[Dict[str, str]]:
    return [
        {"id": key, "label": val["label"], "origin": val["origin"], "language": val["language"]}
        for key, val in sorted(_load()["expanders"].items())
    ]


def model_detail(slug: str) -> Optional[Dict[str, Any]]:
    data = _load()
    model = data["models"].get(slug)
    if not model:
        return None
    return {
        "slug": slug,
        "display": model["display"],
        "provider": model["provider"],
        "endpoint": model["endpoint"],
        "kind": model["kind"],
        "accepts_image": model.get("accepts_image", False),
        "reference": model.get("reference"),
        "params": model["params"],
        "layers": model["layers"],
        "sources": sorted({layer["source"] for layer in model["layers"]}),
    }


def model_list() -> List[Dict[str, Any]]:
    out = []
    for slug in model_slugs():
        m = _load()["models"][slug]
        out.append(
            {
                "slug": slug,
                "display": m["display"],
                "provider": m["provider"],
                "kind": m["kind"],
                "accepts_image": m.get("accepts_image", False),
                "layer_count": len(m["layers"]),
                "active_layer_count": sum(1 for x in m["layers"] if x.get("active")),
            }
        )
    return out


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _disabled_by_override(override: Dict[str, Any], options: Dict[str, Any]) -> bool:
    """True when the caller supplied the value that switches the layer off.

    `disable_with` maps a parameter to the value that disables the layer. The
    literal "<your own>" means any value the caller supplies counts (used for
    the provider's canned negative prompt).
    """
    for key, disabling in override.items():
        if key not in options:
            continue
        if disabling == "<your own>":
            return True
        if options[key] == disabling:
            return True
    return False


def _split_generic(kind: str):
    """Split generic layers into those that apply to this request kind and those
    that are merely possible.

    Honesty rule: a layer that is conditional (only true behind certain proxies)
    or documented for a different request kind is reported under
    `conditional_layers`, with the reason — never counted as an active fact.
    """
    data = _load()
    applicable, conditional = [], []
    for layer in data["generic_layers"]:
        if layer.get("conditional"):
            conditional.append({
                **layer, "active": None,
                "why": f"only applies when the request is routed through a proxy with this behaviour ({layer.get('scope')})",
            })
            continue
        scope = layer.get("applies_to", "any")
        if kind == "unknown":
            applicable.append({**layer, "active": True, "unconfirmed": True})
        elif scope in ("any", kind):
            applicable.append({**layer, "active": True})
        else:
            conditional.append({
                **layer, "active": None,
                "why": f"documented for {scope} requests; this model is a {kind} model",
            })
    return applicable, conditional


def build_preflight(
    model_slug: str,
    prompt: str,
    options: Optional[Dict[str, Any]] = None,
    system_message: Optional[str] = None,
    image_count: int = 0,
) -> Dict[str, Any]:
    """Assemble the upstream payload for (model, prompt) and explain every layer."""
    data = _load()
    options = dict(options or {})
    model = data["models"].get(model_slug)
    unknown = model is None

    layers: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if unknown:
        warnings.append(
            f"No layer facts on record for '{model_slug}'. The layers below are the generic ones, "
            "marked unconfirmed — do not assume this endpoint adds nothing."
        )
    else:
        # Per-model layers, with user overrides applied.
        for layer in model["layers"]:
            override = layer.get("disable_with") or {}
            applied = {k: options[k] for k in override if k in options}
            still_active = layer.get("active", True) and not _disabled_by_override(override, options)
            entry = dict(layer)
            entry["active"] = bool(still_active)
            entry["your_override"] = applied or None
            layers.append(entry)
            if entry["active"] and layer["kind"] in ("rewrite", "expand", "augment"):
                if override:
                    own = [k for k, v in override.items() if v == "<your own>"]
                    knobs = ", ".join(f"{k}={v!r}" for k, v in override.items() if v != "<your own>")
                    tail = ""
                    if knobs:
                        tail += f" To stop it, send {knobs}."
                    if own:
                        tail += f" To replace it, send your own {', '.join(own)}."
                else:
                    tail = ""
                warnings.append(f"{model['display']}: {layer['id']} is active — {layer['detail']}{tail}")

    generic_applicable, conditional_layers = _split_generic("unknown" if unknown else model["kind"])
    layers.extend(generic_applicable)

    # Resolved parameters: provider default unless the caller overrode it.
    params: Dict[str, Any] = {}
    if not unknown:
        for name, spec in model["params"].items():
            if name in options:
                params[name] = {"value": options[name], "source": "your override", "provider_default": spec.get("default")}
            else:
                params[name] = {"value": spec.get("default"), "source": "provider default", "provider_default": spec.get("default")}

    added_chars = 0
    for layer in layers:
        if layer["id"] == "augment.default_negative" and layer.get("active"):
            # length of the provider's own negative prompt, when we know it
            default_neg = ""
            if not unknown:
                default_neg = str(model["params"].get("negative_prompt", {}).get("default") or "")
            added_chars += len(default_neg)

    forwarded = prompt
    messages = None
    system_added = None
    if not unknown and model["kind"] == "chat":
        if system_message:
            system_added = system_message
        else:
            system_added = "You are a helpful assistant."  # injected by the chat template
            warnings.append(
                "No system message supplied: the serving stack's chat template supplies one. "
                "Set system_message to own that slot."
            )
        messages = [
            {"role": "system", "content": system_added},
            {"role": "user", "content": prompt},
        ]
        added_chars += len(system_added)

    full_control_patch = {
        k: v
        for k, v in data["control_presets"]["full_control"]["params"].items()
        if not unknown and k in model["params"]
    }
    if not unknown:
        for layer in model["layers"]:
            for k, v in (layer.get("disable_with") or {}).items():
                if v is not None and v != "<your own>" and k in model["params"]:
                    full_control_patch.setdefault(k, v)

    return {
        "model": model_slug,
        "display": (model or {}).get("display"),
        "provider": (model or {}).get("provider"),
        "endpoint": (model or {}).get("endpoint"),
        "unknown_model": unknown,
        "prompt": {
            "original": prompt,
            "forwarded_as": forwarded,
            "rewritten_upstream": any(
                x["active"] and x["kind"] in ("rewrite", "expand") for x in layers
            ),
            "image_count": image_count,
        },
        "messages": messages,
        "params": params,
        "layers": layers,
        "conditional_layers": conditional_layers,
        "warnings": warnings,
        "full_control_patch": full_control_patch,
        "estimate": {
            "prompt_tokens": _estimate_tokens(prompt),
            "added_chars": added_chars,
            "note": "Token counts are character-based estimates, not tokenizer output.",
        },
        "supports_compile": not unknown and model["kind"] == "video",
        "compile_styles": [s["id"] for s in expander_styles()],
        "version": _load()["version"],
    }


def build_compile_request(
    prompt: str,
    style: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
) -> Dict[str, Any]:
    """Pick an open-source expander's system prompt and build the upstream request."""
    data = _load()
    expander = data["expanders"].get(style)
    if not expander:
        raise KeyError(style)
    template = expander["system_prompt"]
    # Hunyuan's and Kandinsky's templates carry an inline placeholder and are
    # sent as a single user message; Wan's is a plain system prompt and the
    # user prompt goes in the user turn. Mirror each upstream implementation.
    if "{input}" in template or "{prompt}" in template:
        filled = template.replace("{input}", prompt).replace("{prompt}", prompt)
        messages = [{"role": "user", "content": filled}]
        system_prompt = None
    else:
        system_prompt = template
        messages = [
            {"role": "system", "content": template},
            {"role": "user", "content": prompt},
        ]
    return {
        "style": style,
        "label": expander["label"],
        "origin": expander["origin"],
        "system_prompt": system_prompt,
        "request": {
            "model": model,
            "temperature": temperature,
            "messages": messages,
        },
    }
