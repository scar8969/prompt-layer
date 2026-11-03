"""Factories: a brief turned into a multi-shot canvas, not a magic campaign.

What a factory is
-----------------
A factory is a **planner**, in the same sense as the Director's Panel. It takes
what the author wrote and produces a graph of generate nodes: a UGC shoot's
scenes, a fashion lookbook's looks, or a character's photodump across catalogue
styles. It calls no model, writes no row and spends nothing; the canvas it
produces is run through the ordinary queue, one real generation per node.

What it refuses to do
---------------------
It does not write claims, prices or testimonials. Every node's prompt contains
the author's product and benefit verbatim plus craft wording for the shot; a
factory with no product is refused rather than filled with a placeholder that
would produce an ad about nothing.

The three factories
--------------------
``ugc``
    Hook, proof, demonstration, call to action — the four beats a short vertical
    ad actually needs, each a still with a caption the author can edit.

``fashion``
    Full length, detail, movement, editorial — four looks over one outfit, using
    the Director's vocabulary for body, glass and light when it is supplied.

``photodump``
    One character, several catalogue styles. It is the one factory whose styles
    come from a document (the preset catalogue) rather than from the author, and
    each node names the preset it came from.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import VALIDATION_FAILED, ApiError
from .presets_data import PRESETS_BY_KEY, presets_for

#: The factories this platform can plan.
KINDS: Tuple[str, ...] = ("ugc", "fashion", "photodump")

KIND_LABELS: Dict[str, str] = {
    "ugc": "UGC shoot",
    "fashion": "Fashion lookbook",
    "photodump": "Photodump",
}

#: Upper bound on nodes one plan may produce. A campaign is a handful of shots,
#: not a batch of two hundred; the cap is also what keeps a typo in `count` from
#: turning into a very large bill.
MAX_NODES = 12

#: The four UGC beats, in the order a short ad reads.
UGC_BEATS: Tuple[Tuple[str, str, str], ...] = (
    (
        "hook",
        "Hook",
        "a close, direct-to-camera frame that stops the scroll; the product is in the "
        "first third of the frame",
    ),
    (
        "proof",
        "Proof",
        "the product in use, hands in frame, natural light, no studio gloss",
    ),
    (
        "demo",
        "Demonstration",
        "a detail frame showing the one thing the product does, held close enough to read",
    ),
    (
        "cta",
        "Call to action",
        "the product alone on a clean surface with room for a line of type below it",
    ),
)

#: The four fashion looks, and the shot each one is.
FASHION_LOOKS: Tuple[Tuple[str, str, str], ...] = (
    ("full_length", "Full length", "full-length frame, feet to head, outfit readable as a silhouette"),
    ("detail", "Detail", "waist-up detail of fabric, seam and hardware at close range"),
    ("movement", "Movement", "mid-stride, fabric caught in motion, slight blur only at the hem"),
    ("editorial", "Editorial", "three-quarter editorial frame, negative space to one side"),
)


def _require_text(value: Any, field: str, limit: int = 400) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ApiError(VALIDATION_FAILED, "%s is required." % field.replace("_", " ").capitalize(), field=field)
    return text[:limit]


def _optional(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def plan(
    kind: str,
    *,
    model_id: str,
    product: str,
    benefit: str = "",
    audience: str = "",
    subject: str = "",
    body: str = "",
    lens: str = "",
    lighting: str = "",
    palette: str = "",
    styles: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Plan one factory run as canvas nodes and edges.

    Pure: the same input produces the same graph. The `model_id` is required
    because a generate node without one cannot be planned, and the plan is
    refused here rather than stored as a canvas that fails at run time.
    """
    chosen = str(kind or "").strip().lower()
    if chosen not in KINDS:
        raise ApiError(
            VALIDATION_FAILED,
            "Unknown factory '%s'. The factories are %s." % (chosen, ", ".join(KINDS)),
            field="kind",
        )
    model = _require_text(model_id, "model_id", 200)
    product = _require_text(product, "product", 300)
    benefit = _optional(benefit, 300)
    audience = _optional(audience, 200)
    subject = _optional(subject, 300) or product
    craft = _craft(body=body, lens=lens, lighting=lighting, palette=palette)

    nodes: List[Dict[str, Any]] = []

    def add(node_id: str, label: str, prompt: str, index: int) -> None:
        nodes.append(
            {
                "id": node_id,
                "kind": "generate",
                "label": label,
                "position": {"x": 40 + (index % 3) * 260, "y": 40 + (index // 3) * 200},
                "prompt": prompt,
                "model_id": model,
                "count": 1,
            }
        )

    if chosen == "ugc":
        for index, (beat_key, beat_label, direction) in enumerate(UGC_BEATS):
            prompt = _compose(subject, direction, benefit, audience, craft)
            add("ugc-" + beat_key, beat_label, prompt, index)
    elif chosen == "fashion":
        for index, (look_key, look_label, direction) in enumerate(FASHION_LOOKS):
            prompt = _compose(subject, direction, benefit, audience, craft)
            add("fashion-" + look_key, look_label, prompt, index)
    else:
        picks = _styles(styles)
        for index, key in enumerate(picks):
            entry = PRESETS_BY_KEY[key]
            template = str(entry.get("prompt_template") or "").strip()
            prompt = "%s The subject is %s." % (template.rstrip(".") + ".", subject)
            if benefit:
                prompt += " %s." % benefit.rstrip(".")
            add("photodump-" + key.replace(".", "-"), str(entry.get("name") or key), prompt, index)

    return {
        "kind": chosen,
        "label": KIND_LABELS[chosen],
        "model_id": model,
        "product": product,
        "audience": audience,
        "benefit": benefit,
        "nodes": nodes,
        "edges": [],
        "summary": {
            "node_count": len(nodes),
            "generate_count": len(nodes),
            "note_count": 0,
            "stages": [{"node_id": node["id"], "label": node["label"], "kind": "generate"} for node in nodes],
            "order": [node["id"] for node in nodes],
        },
        "notes": {
            "copy": "Composed from your product and benefit verbatim; a factory writes no claims.",
            "run": "Every node is one ordinary generation job on the model you chose.",
        },
    }


def _craft(*, body: str, lens: str, lighting: str, palette: str) -> str:
    """The Director's vocabulary, only when the caller supplied it."""
    parts = []
    for value in (body, lens, lighting, palette):
        text = _optional(value, 80)
        if text:
            parts.append(text)
    return ", ".join(parts)


def _compose(subject: str, direction: str, benefit: str, audience: str, craft: str) -> str:
    """One shot prompt: the subject, the direction, the claim, the craft."""
    parts = [subject, direction]
    if benefit:
        parts.append(benefit.rstrip("."))
    if audience:
        parts.append("made for %s" % audience.rstrip("."))
    if craft:
        parts.append(craft)
    parts.append("no rendered text, no logos")
    return ", ".join(parts) + "."


def _styles(raw: Optional[Sequence[str]]) -> List[str]:
    """The catalogue styles a photodump covers.

    Defaults to the first eight image presets, in catalogue order, so a caller
    that names no styles still gets a real, sourced set rather than an invented
    one. A style outside the catalogue is refused by name.
    """
    available = [entry["key"] for entry in presets_for("image")]
    if raw:
        if not isinstance(raw, (list, tuple)):
            raise ApiError(VALIDATION_FAILED, "'styles' must be a list of catalogue keys.", field="styles")
        picked: List[str] = []
        for item in raw:
            key = str(item).strip()
            if key not in PRESETS_BY_KEY:
                raise ApiError(
                    VALIDATION_FAILED,
                    "No catalogue style has the key '%s'." % key,
                    field="styles",
                )
            if key not in picked:
                picked.append(key)
        if len(picked) > MAX_NODES:
            raise ApiError(
                VALIDATION_FAILED,
                "A photodump covers at most %d styles." % MAX_NODES,
                field="styles",
            )
        return picked
    return available[: min(8, MAX_NODES)]


def describe() -> Dict[str, Any]:
    """What each factory produces, for the screen that offers them."""
    return {
        "factories": [
            {
                "kind": "ugc",
                "label": KIND_LABELS["ugc"],
                "summary": "Four vertical beats: hook, proof, demonstration, call to action.",
                "node_count": len(UGC_BEATS),
            },
            {
                "kind": "fashion",
                "label": KIND_LABELS["fashion"],
                "summary": "Four looks over one outfit, with the camera vocabulary you supply.",
                "node_count": len(FASHION_LOOKS),
            },
            {
                "kind": "photodump",
                "label": KIND_LABELS["photodump"],
                "summary": "One subject across catalogue styles, each node naming its preset.",
                "node_count": min(8, MAX_NODES),
            },
        ]
    }
