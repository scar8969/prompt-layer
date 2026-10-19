"""The prompt assistant: deterministic rewording, not a model call.

Three rules govern this module, and they exist because a prompt assistant is the
easiest place in a creative tool to become dishonest.

1. It never claims to be an AI. Every response carries ``provider`` =
   ``"local-rules"`` and ``method`` naming the transformation applied, and
   ``is_mock`` is false because nothing here is a placeholder for a model -- it
   is the actual, complete behaviour of this feature.
2. It never silently replaces the user's words. It returns a suggestion plus an
   explicit ``notes`` list saying what was added or moved, so the client can show
   both and let the author decide. The client already previews before applying.
3. It never invents subject matter. It reorganises what the user wrote and adds
   only craft vocabulary from closed lists below -- framing, light, lens, mood --
   and it says which ones it added. If the workspace has connected an LLM it can
   do better, but this feature does not call out to anything: nothing leaves the
   server, so no unapproved provider ever sees a user's prompt.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Tuple

from ..errors import VALIDATION_FAILED, ApiError

#: Closed vocabulary. Adding a word here is a deliberate product decision, not a
#: guess, and every one of them is a technical term a photographer or director
#: would recognise rather than a vague adjective.
LIGHT_WORDS: Tuple[str, ...] = (
    "golden hour", "overcast", "hard noon sun", "rim light", "soft window light",
    "candlelight", "neon", "practical lights", "moonlight", "studio softbox",
)
FRAMING_WORDS: Tuple[str, ...] = (
    "wide establishing shot", "medium shot", "close-up", "macro",
    "low angle", "high angle", "over-the-shoulder", "dutch tilt",
)
LENS_WORDS: Tuple[str, ...] = (
    "24mm", "35mm", "50mm", "85mm", "135mm", "anamorphic", "tilt-shift",
)
MOOD_WORDS: Tuple[str, ...] = (
    "quiet", "tense", "hopeful", "melancholy", "playful", "austere", "nostalgic",
)

#: Words that already mean the user has said it, so nothing is added twice.
_ALREADY = {
    "light": LIGHT_WORDS,
    "framing": FRAMING_WORDS,
    "lens": LENS_WORDS,
    "mood": MOOD_WORDS,
}

#: Aspect ratio and quality phrasing that changes nothing about the scene.
QUALITY_HINTS = ("high detail", "sharp focus", "natural colour")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _has(prompt: str, words: Tuple[str, ...]) -> bool:
    lowered = prompt.lower()
    return any(word.lower() in lowered for word in words)


def describe_capabilities() -> Dict[str, Any]:
    """What this assistant can and cannot do, for the client to render honestly."""
    return {
        "provider": "local-rules",
        "external_call": False,
        "sends_prompts_to_a_third_party": False,
        "operations": [
            {"key": "structure", "label": "Structure into labelled sections"},
            {"key": "expand", "label": "Add craft vocabulary from a closed list"},
            {"key": "condense", "label": "Trim to the subject and one clause"},
            {"key": "to_video", "label": "Turn an image prompt into a shot description"},
        ],
        "vocabulary": {
            "light": list(LIGHT_WORDS),
            "framing": list(FRAMING_WORDS),
            "lens": list(LENS_WORDS),
            "mood": list(MOOD_WORDS),
        },
        "limitations": [
            "It reorganises your words; it does not understand the scene.",
            "Every addition comes from the fixed lists above and is named in notes.",
            "It never calls a model, so it works with no connection configured.",
        ],
    }


def _sections(prompt: str) -> Dict[str, str]:
    """Split a prompt into the parts a photographer would name.

    This is deliberately crude and stated as such: a comma-separated list is
    partitioned by which closed vocabulary each clause mentions. A clause that
    matches nothing is treated as part of the subject, because the subject is the
    only part that must survive whatever else happens.
    """
    pieces = [piece.strip(" .") for piece in re.split(r"[,;\n]", prompt) if piece.strip(" .")]
    out: Dict[str, List[str]] = {"subject": [], "light": [], "framing": [], "lens": [], "mood": [], "other": []}
    for piece in pieces:
        placed = False
        for name, words in _ALREADY.items():
            if any(word.lower() in piece.lower() for word in words):
                out[name].append(piece)
                placed = True
                break
        if not placed:
            out["subject"].append(piece)
    return {name: ", ".join(values) for name, values in out.items()}


def improve(prompt: str, *, operation: str = "structure", negative_prompt: str = "") -> Dict[str, Any]:
    """Rewrite a prompt, and say exactly what changed.

    Refuses an empty prompt (``validation_failed``), because there is nothing to
    improve and returning the empty string as a suggestion would look like a
    successful rewrite.
    """
    text = _norm(prompt)
    if not text:
        raise ApiError(
            VALIDATION_FAILED,
            "There is nothing to improve: the prompt is empty.",
            field="prompt",
        )
    if operation not in ("structure", "expand", "condense", "to_video"):
        raise ApiError(
            VALIDATION_FAILED,
            "The operation must be structure, expand, condense or to_video.",
            field="operation",
            details={"allowed": ["structure", "expand", "condense", "to_video"]},
        )
    parts = _sections(text)
    notes: List[str] = []
    lines: List[str] = []

    subject = parts["subject"] or text
    lines.append(subject)

    if operation == "condense":
        first = subject.split(",")[0].strip()
        suggestion = first
        notes.append("Kept the first clause and dropped the rest.")
        return _result(suggestion, notes, operation, prompt, negative_prompt)

    if operation in ("structure", "to_video"):
        for name, label in (("framing", "Shot"), ("light", "Light"), ("lens", "Lens"), ("mood", "Mood")):
            if parts[name]:
                lines.append("%s: %s" % (label, parts[name]))
                notes.append("Moved an existing %s clause under its own label." % name)

    if operation == "expand":
        added = []
        for name, words, label in (
            ("light", LIGHT_WORDS, "Light"),
            ("framing", FRAMING_WORDS, "Shot"),
            ("lens", LENS_WORDS, "Lens"),
        ):
            if not parts[name]:
                chosen = words[0]
                lines.append("%s: %s" % (label, chosen))
                added.append("%s = %s" % (name, chosen))
        if not _has(text, QUALITY_HINTS):
            lines.append("Quality: " + ", ".join(QUALITY_HINTS))
            added.append("quality = " + ", ".join(QUALITY_HINTS))
        if added:
            notes.append("Added, from the fixed list: " + "; ".join(added) + ".")
        else:
            notes.append("Nothing was added: the prompt already covered every category.")

    if operation == "to_video":
        lines.append("Motion: slow, continuous push in")
        notes.append(
            "Added a motion line. This is wording, not a camera parameter: whether a "
            "model honours it depends on the model, and the studio labels camera moves "
            "as prompt wording when the model declares no native control."
        )

    suggestion = "\n".join(line for line in lines if line.strip())
    if suggestion.strip() == text:
        notes.append("The rewrite is identical to the original, so applying it changes nothing.")
    return _result(suggestion, notes, operation, prompt, negative_prompt)


def _result(
    suggestion: str, notes: List[str], operation: str, original: str, negative_prompt: str
) -> Dict[str, Any]:
    """One response shape, identical for every operation."""
    return {
        "suggestion": suggestion,
        "negative_prompt": _norm(negative_prompt),
        "original": original,
        "changed": suggestion.strip() != _norm(original),
        "notes": notes,
        "operation": operation,
        "provider": "local-rules",
        "method": "deterministic",
        "external_call": False,
        "is_mock": False,
    }
