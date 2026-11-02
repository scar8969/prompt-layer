"""The Director's Panel and the preset style cards.

  GET  /api/cinema/vocabulary        every control, option, phrase and note
  POST /api/cinema/compile           one brief → one prompt, split into segments
  GET  /api/preset-cards/{key}       the style card for one catalogue preset

Nothing here calls a provider, writes a row, or spends anything. The panel is a
compiler: in go choices, out a prompt and the ledger of what produced it. That
is why the compile route is a POST with a body and not a GET with twenty query
parameters — the brief is the document.
"""

from __future__ import annotations

from typing import Any, Dict

from .. import cinema
from ..errors import NOT_FOUND, VALIDATION_FAILED
from ..http import ApiError, RequestContext, Response
from ..preset_cards import card_svg
from ..presets_data import PRESETS_BY_KEY

MAX_SUBJECT = 1200
MAX_SETTING = 600
MAX_NEGATIVE = 800

#: Every control the compiler reads. A key outside this list is ignored rather
#: than echoed into a prompt, so a caller cannot smuggle arbitrary text into the
#: "setting" clause through an unknown field name.
BRIEF_FIELDS = (
    "subject",
    "setting",
    "shots",
    "angles",
    "compositions",
    "bodies",
    "lenses",
    "focal_lengths",
    "apertures",
    "shutters",
    "lighting",
    "palettes",
    "grain",
    "moods",
    "movement",
    "aspects",
    "negative",
)


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Collapse newlines: a prompt is one paragraph, and a pasted brief with hard
    # breaks would otherwise read as a list of shots to some models.
    return " ".join(value.split())[:limit]


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def vocabulary(request: RequestContext) -> Response:
        """The panel: groups, controls, options, and the phrase each one adds."""
        document = cinema.vocabulary_document()
        return Response.json(
            {
                "vocabulary": document,
                "source": "studio/cinema.py — craft vocabulary, not provider behaviour",
                "provider_layers": "See /api/prompt/models for what each endpoint adds to a prompt.",
            }
        )

    def compile_brief(request: RequestContext) -> Response:
        """Compile one brief. Deterministic: same input, same prompt, always."""
        body = request.json()
        brief: Dict[str, Any] = {}
        for field in BRIEF_FIELDS:
            if field not in body:
                continue
            if field in ("subject", "setting", "negative"):
                brief[field] = _text(
                    body.get(field),
                    MAX_SUBJECT if field == "subject" else MAX_SETTING if field == "setting" else MAX_NEGATIVE,
                )
            else:
                brief[field] = _text(body.get(field), 64)

        modality = str(body.get("modality") or "image")
        if modality not in ("image", "video"):
            raise ApiError(
                VALIDATION_FAILED,
                "'modality' must be 'image' or 'video'.",
                field="modality",
            )
        brief["modality"] = modality

        disabled = body.get("disabled") or []
        if not isinstance(disabled, list):
            raise ApiError(VALIDATION_FAILED, "'disabled' must be a list of segment keys.", field="disabled")
        allowed = {key for key, _ in cinema.SEGMENT_ORDER}
        brief["disabled"] = [str(item) for item in disabled if str(item) in allowed]

        # A choice outside the vocabulary is refused by name rather than silently
        # dropped: if the client sends a stale key, the prompt would quietly lose
        # a clause and the ledger would look complete. The MCP tool calls the same
        # helper, so the two surfaces cannot drift.
        cinema.validate_brief(brief)

        result = cinema.compile_brief(brief)
        return Response.json(
            {
                "compiled": result,
                "brief": brief,
                "source": "studio/cinema.py",
                "notes": {
                    "injection": "Every clause names the control that produced it; nothing is paraphrased.",
                    "provider": "The provider's own additive layers are a separate dataset — see Prompt Lab.",
                },
            }
        )

    def preset_card(request: RequestContext) -> Response:
        """The style card for one catalogue preset, as SVG."""
        key = request.params["key"]
        if key.endswith(".svg"):
            key = key[: -len(".svg")]
        entry = PRESETS_BY_KEY.get(key)
        if entry is None:
            raise ApiError(NOT_FOUND, "No preset with that key.")
        svg = card_svg(entry)
        return Response.bytes(
            svg.encode("utf-8"),
            content_type="image/svg+xml; charset=utf-8",
            headers={
                # Deterministic art: a card for a key never changes, so it can be
                # cached hard. A version bump in card_svg() changes the URL only
                # if the caller asks for one, so the header stays conservative.
                "Cache-Control": "private, max-age=604800",
                "X-Style-Card": "generated-from-preset-tokens",
            },
        )

    router.add("GET", "/api/cinema/vocabulary", vocabulary)
    router.add("POST", "/api/cinema/compile", compile_brief)
    router.add("GET", "/api/preset-cards/{key}", preset_card)
