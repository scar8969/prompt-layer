"""Marketing Studio: a product page in, a campaign of real generations out.

What this can and cannot do
---------------------------
"Product URL to ad" splits into two halves, and only one of them is a model call:

1. **Reading the page.** Fetch the URL through the same SSRF-guarded egress client
   every other outbound call uses, pull the title, description and Open Graph
   tags, and build a *brief*: the product, the benefit, the audience, the hook.
   This is deterministic text work. No model is called, so nothing is invented
   beyond what the page actually says.
2. **Rendering the campaign.** One generation job per format, queued through the
   ordinary generation service on the user's own key. The platform does not
   generate the ad copy with a model of its own and does not claim to.

That split is the honest one for a BYO-key product. A brief built from a page's
own metadata is checkable; copy "written by AI" from a page nobody read is not.

The brief is a document, like a canvas: it is validated, it names where every
field came from, and a field the page did not state stays empty with a note
saying so. It never invents a price, a claim or an audience.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from .errors import VALIDATION_FAILED, ApiError

#: The formats a campaign can render. Each is one generator, not a promise: the
#: copy is composed from the brief, and the picture is one real generation.
FORMATS: Tuple[str, ...] = (
    "square_1x1",
    "portrait_4x5",
    "story_9x16",
    "landscape_16x9",
)

FORMAT_LABELS: Dict[str, str] = {
    "square_1x1": "Square 1:1 (feed)",
    "portrait_4x5": "Portrait 4:5 (feed)",
    "story_9x16": "Story 9:16",
    "landscape_16x9": "Landscape 16:9 (web)",
}

#: Aspect ratios the platform already validates against a model profile.
FORMAT_ASPECTS: Dict[str, str] = {
    "square_1x1": "1:1",
    "portrait_4x5": "4:5",
    "story_9x16": "9:16",
    "landscape_16x9": "16:9",
}

#: The one tone vocabulary. A tone changes the sentence structure the brief
#: composes; it does not call a model to rewrite anything.
TONES: Tuple[str, ...] = ("direct", "warm", "editorial", "playful", "technical")

#: Pages are read with the same SSRF guard as every other outbound call, and a
#: marketing page is bounded far below a media download: a product page that
#: needs more than 2 MB of HTML is not a product page.
MAX_PAGE_BYTES = 2 * 1024 * 1024

MAX_TITLE = 200
MAX_DESCRIPTION = 1200
MAX_FEATURES = 8


def _error(message: str, field: str = "") -> ApiError:
    return ApiError(VALIDATION_FAILED, message, field=field)


# ---------------------------------------------------------------------------
# Reading a page
# ---------------------------------------------------------------------------


_META = re.compile(r"<meta\s+[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'([a-zA-Z:_-]+)\s*=\s*"([^"]*)"', re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_DESCRIPTION = re.compile(
    r'<meta\s+[^>]*name\s*=\s*"description"\s+[^>]*content\s*=\s*"([^"]*)"', re.IGNORECASE
)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _text_of(raw: str) -> str:
    """Visible text from an HTML fragment, with tags removed and space collapsed."""
    return _WHITESPACE.sub(" ", _TAGS.sub(" ", raw or "")).strip()


def parse_product_page(html: str, url: str) -> Dict[str, Any]:
    """The facts a page states about itself, and where each one came from.

    Pure and deterministic. A field the page does not state is ``None`` with a
    note, never a guess: a brief that invented a product benefit would produce an
    ad making a claim nobody can check.
    """
    source = str(html or "")
    facts: Dict[str, Any] = {
        "url": url,
        "title": None,
        "description": None,
        "site_name": None,
        "image": None,
        "features": [],
        "sources": {},
    }

    for tag in _META.findall(source):
        attributes = {name.lower(): value for name, value in _ATTR.findall(tag)}
        key = (attributes.get("property") or attributes.get("name") or "").lower()
        content = _WHITESPACE.sub(" ", attributes.get("content") or "").strip()
        if not content:
            continue
        if key in ("og:title", "twitter:title") and not facts["title"]:
            facts["title"] = content[:MAX_TITLE]
            facts["sources"]["title"] = key
        elif key in ("og:description", "twitter:description") and not facts["description"]:
            facts["description"] = content[:MAX_DESCRIPTION]
            facts["sources"]["description"] = key
        elif key == "og:site_name":
            facts["site_name"] = content[:120]
            facts["sources"]["site_name"] = key
        elif key in ("og:image", "twitter:image") and not facts["image"]:
            facts["image"] = content[:2000]
            facts["sources"]["image"] = key

    if not facts["title"]:
        match = _TITLE.search(source)
        if match:
            facts["title"] = _text_of(match.group(1))[:MAX_TITLE]
            facts["sources"]["title"] = "title"
    if not facts["title"]:
        match = _H1.search(source)
        if match:
            facts["title"] = _text_of(match.group(1))[:MAX_TITLE]
            facts["sources"]["title"] = "h1"
    if not facts["description"]:
        match = _DESCRIPTION.search(source)
        if match:
            facts["description"] = _WHITESPACE.sub(" ", match.group(1)).strip()[:MAX_DESCRIPTION]
            facts["sources"]["description"] = "meta[name=description]"

    # Features come from the page's own list items. They are trimmed and capped
    # because a marketing brief has room for a few, and an item long enough to be
    # a paragraph is not a feature.
    for match in re.finditer(r"<li[^>]*>(.*?)</li>", source, re.IGNORECASE | re.DOTALL):
        item = _text_of(match.group(1))
        if 8 <= len(item) <= 160:
            facts["features"].append(item)
        if len(facts["features"]) >= MAX_FEATURES:
            break
    if facts["features"]:
        facts["sources"]["features"] = "li"

    facts["missing"] = [
        name
        for name in ("title", "description")
        if not facts.get(name)
    ]
    return facts


# ---------------------------------------------------------------------------
# Building the brief
# ---------------------------------------------------------------------------


def _sentence(text: str) -> str:
    text = _WHITESPACE.sub(" ", str(text or "")).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _trim(text: str, limit: int) -> str:
    text = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(",;:") + "…"


def build_brief(
    facts: Mapping[str, Any],
    *,
    audience: str = "",
    benefit: str = "",
    tone: str = "direct",
) -> Dict[str, Any]:
    """Compose a campaign brief from page facts plus what the author supplied.

    Three inputs and their provenance:

    * the page states the product and (often) what it does -- used verbatim;
    * the author states the audience and the benefit -- used verbatim;
    * a tone restructures the hook sentence, and only that sentence.

    A brief with no benefit and no description is returned with a warning rather
    than a filled-in claim, because the ad would otherwise be making one up.
    """
    normalised_tone = str(tone or "direct").strip().lower()
    if normalised_tone not in TONES:
        raise _error("'tone' must be one of %s." % ", ".join(TONES), field="tone")

    title = _sentence(_trim(str(facts.get("title") or ""), MAX_TITLE))
    description = _sentence(_trim(str(facts.get("description") or ""), MAX_DESCRIPTION))
    product = title or "the product"
    # The claim as the author typed it, for embedding mid-sentence in a prompt,
    # and sentence-cased for the hook where it starts a sentence.
    claim_raw = _trim(benefit, 240) or _trim(str(facts.get("description") or ""), MAX_DESCRIPTION)
    claim = _sentence(claim_raw)

    hooks = {
        "direct": "{product}. {claim}",
        "warm": "Meet {product} — {claim}",
        "editorial": "{product}: {claim}",
        "playful": "{product}. Yes, really. {claim}",
        "technical": "{product} — {claim}",
    }
    template = hooks[normalised_tone]
    hook = template.format(product=product, claim=claim or "").strip()
    hook = re.sub(r"\s+([—.,])", r" \1", hook).replace(" .", ".")

    warnings: List[str] = []
    missing = list(facts.get("missing") or [])
    if "title" in missing:
        warnings.append("The page stated no title, so the product name is a placeholder.")
    if not claim:
        warnings.append(
            "Neither the page nor the author stated a benefit, so the copy has no claim. "
            "Add one before rendering: an ad without a claim is a picture of a product."
        )
    if not audience.strip():
        warnings.append("No audience was given, so none is claimed in the copy.")

    formats = []
    for key in FORMATS:
        formats.append(
            {
                "key": key,
                "label": FORMAT_LABELS[key],
                "aspect_ratio": FORMAT_ASPECTS[key],
                "headline": _trim(hook or product, 90),
                "caption": _trim(
                    " ".join(part for part in (claim, audience and "For %s." % audience, "") if part),
                    300,
                ),
                "prompt": _image_prompt(product, description, key, normalised_tone, claim_raw),
            }
        )

    return {
        "product": product,
        "url": str(facts.get("url") or ""),
        "site_name": str(facts.get("site_name") or ""),
        "description": description,
        "features": list(facts.get("features") or [])[:MAX_FEATURES],
        "image_reference": str(facts.get("image") or ""),
        "audience": _trim(audience, 200),
        "benefit": _sentence(_trim(benefit, 240)),
        "tone": normalised_tone,
        "hook": hook,
        "hooks": [{"tone": name, "text": hooks[name].format(product=product, claim=claim or "").strip()} for name in TONES],
        "formats": formats,
        "warnings": warnings,
        "sources": dict(facts.get("sources") or {}),
        "notes": {
            "copy": "Composed from the page's own metadata and what the author typed. No model wrote it.",
            "render": "Each format queues one real generation on your own key; this route does not call a provider.",
        },
    }


def _image_prompt(product: str, description: str, fmt: str, tone: str, claim: str = "") -> str:
    """A product-shot prompt for one format, built from stated facts only."""
    framing = {
        "square_1x1": "centred product hero shot",
        "portrait_4x5": "vertical product hero shot with headroom",
        "story_9x16": "tall product hero shot, product in the lower third",
        "landscape_16x9": "wide product hero shot with negative space to the left",
    }[fmt if fmt in FORMATS else "square_1x1"]
    light = {
        "direct": "clean seamless backdrop, hard key from camera left",
        "warm": "warm window light, soft falloff, linen surface",
        "editorial": "single softbox, deep shadow, gallery backdrop",
        "playful": "bright even light, saturated backdrop, playful props",
        "technical": "even product lighting, neutral backdrop, visible detail",
    }[tone if tone in TONES else "direct"]
    tail = _trim(claim or description, 160)
    parts = ["%s of %s" % (framing, product), light]
    if tail:
        # The claim is the author's own words; the picture is asked to carry it,
        # not to illustrate a paraphrase.
        parts.append("the product is %s" % tail.rstrip("."))
    parts.append("no text, no logos rendered")
    return ", ".join(parts) + "."


def validate_formats(raw: Any) -> List[str]:
    """The formats to render, refused by name when one is not a real format."""
    if raw in (None, "", []):
        return list(FORMATS)
    if not isinstance(raw, (list, tuple)):
        raise _error("'formats' must be a list of format keys.", field="formats")
    chosen: List[str] = []
    for item in raw:
        key = str(item).strip()
        if key not in FORMATS:
            raise _error(
                "Unknown format '%s'. The formats are %s." % (key, ", ".join(FORMATS)),
                field="formats",
            )
        if key not in chosen:
            chosen.append(key)
    if not chosen:
        raise _error("Choose at least one format to render.", field="formats")
    return chosen
