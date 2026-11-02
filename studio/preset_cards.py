"""Style cards: a visual identity for each preset, drawn from the preset itself.

Higgsfield's preset library is a wall of pictures. This catalogue has no
pictures — it has sentences — and faking a photograph would be the one thing a
prompt-transparency tool cannot do. So each preset gets a **style card** instead:
a small, deterministic piece of vector art composed from that preset's own
tokens, labelled as a style card in the interface.

What the card is:

* deterministic — the same preset key always produces the same card, byte for
  byte, so it can be cached and tested;
* honest — no photograph, no model call, no implication that this is what the
  endpoint will return. It is a colour and composition signature, like a
  moodboard chip;
* derived — the hue comes from the grade words in the prompt ("golden", "neon",
  "blue hour", "monochrome"), the composition from its framing words
  ("overhead", "stairwell", "close-up"), and the light direction from whichever
  side the prompt says the light comes from.

What it is not: a preview image. The interface says "style card" wherever one
appears, and a real frame is only ever shown when a real generation produced it.

Pure functions, standard library only.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Mapping, Tuple

WIDTH = 480
HEIGHT = 600
CARD_VERSION = "2"

#: Category → the base composition, before the preset's own words refine it.
FAMILY_BY_CATEGORY: Dict[str, str] = {
    "portraiture": "portrait",
    "fashion-editorial": "portrait",
    "character-design": "portrait",
    "restoration": "portrait",
    "product": "object",
    "food": "object",
    "macro": "bokeh",
    "texture-pattern": "bokeh",
    "architecture": "architecture",
    "interior": "interior",
    "urban": "urban",
    "landscape": "landscape",
    "wildlife": "landscape",
    "aerial": "landscape",
    "landscape-drone": "landscape",
    "automotive": "automotive",
    "poster-graphic": "graphic",
    "logo-mark": "graphic",
    "editorial-illustration": "graphic",
    "3d-render": "object",
    "abstract": "abstract",
    "concept-art": "landscape",
    "cinematic-scene": "portrait",
    "product-film": "object",
    "fashion-film": "portrait",
    "music-video": "urban",
    "social-short": "portrait",
    "explainer": "graphic",
    "documentary": "urban",
    "timelapse": "landscape",
    "action-sport": "urban",
    "dialogue-scene": "portrait",
    "food-film": "object",
    "character-animation": "portrait",
    "abstract-loop": "abstract",
    "title-sequence": "graphic",
}

#: Grade words → (hue, saturation), in priority order. The first match wins, so
#: the list reads from the most specific colour story to the most generic.
HUE_WORDS: Tuple[Tuple[Tuple[str, ...], int, int], ...] = (
    (("monochrome", "black and white", "noir", "silver", "bleach"), 210, 6),
    (("neon", "magenta", "cyan"), 305, 62),
    (("blue hour", "moonlight", "nocturnal", "cold", "cool", "steel"), 205, 40),
    (("golden", "amber", "tungsten", "fire", "sunset", "warm"), 30, 45),
    (("sepia", "archive", "restoration", "aged"), 34, 22),
    (("green", "forest", "olive", "jungle", "moss"), 105, 32),
    (("red", "scarlet", "rust", "crimson", "blood"), 8, 48),
    (("pastel", "milk", "soft", "powder"), 340, 24),
    (("cross-processed", "cross process"), 160, 30),
    (("concrete", "grey", "gray", "overcast", "industrial"), 210, 10),
    (("teal", "aqua", "tropical"), 180, 38),
    (("purple", "violet", "lilac"), 275, 34),
)

#: Framing words → a scene variant. More specific than the category default.
VARIANT_WORDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("top-down", "topdown", "overhead", "straight down", "flat lay", "flatlay"), "topdown"),
    (("stairwell", "staircase", "stairs", "steps"), "stairs"),
    (("corridor", "hallway", "tunnel", "passage"), "corridor"),
    (("colonnade", "arches", "arcade", "facade", "grid of windows"), "arches"),
    (("close-up", "close up", "extreme close", "detail"), "closeup"),
    (("two people", "couple", "embrace", "hand in hand"), "two"),
    (("bottle", "perfume", "flask", "canister"), "bottle"),
    (("mountain", "ridge", "valley", "dune", "cliff"), "ridges"),
    (("aerial", "drone", "from above"), "aerial"),
    (("silhouette", "backlit", "back-lit"), "silhouette"),
)


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _rng(seed: int):
    """A tiny deterministic generator: xorshift32, no imports, no global state."""
    state = seed or 0x9E3779B9

    def nxt() -> float:
        nonlocal state
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        return (state & 0xFFFFFFFF) / 0xFFFFFFFF

    return nxt


def _text_of(preset: Mapping[str, Any]) -> str:
    return " ".join(
        str(preset.get(field) or "")
        for field in ("name", "description", "prompt_template", "negative_template")
    ).lower()


def _hsl(hue: float, saturation: float, lightness: float) -> str:
    """HSL → hex, so a hue can be rotated in degrees and still land in SVG."""
    h = (hue % 360) / 360
    s = max(0.0, min(1.0, saturation / 100))
    l = max(0.0, min(1.0, lightness / 100))
    if s == 0:
        value = round(l * 255)
        return "#%02x%02x%02x" % (value, value, value)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def channel(t: float) -> float:
        t = t % 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    return "#%02x%02x%02x" % tuple(round(channel(h + offset) * 255) for offset in (1 / 3, 0, -1 / 3))


def _spec(preset: Mapping[str, Any]) -> Dict[str, Any]:
    """Read the preset's own words into a rendering spec."""
    key = str(preset.get("key") or preset.get("slug") or preset.get("id") or "preset")
    seed = _seed(key)
    text = _text_of(preset)

    hue, saturation = 210, 26
    for words, candidate_hue, candidate_saturation in HUE_WORDS:
        if any(word in text for word in words):
            hue, saturation = candidate_hue, candidate_saturation
            break
    # A per-preset rotation keeps two presets in one category from being twins.
    hue = (hue + (seed % 29) - 14) % 360

    category = str(preset.get("category") or "abstract")
    family = FAMILY_BY_CATEGORY.get(category, "abstract")
    variant = ""
    for words, candidate in VARIANT_WORDS:
        if any(word in text for word in words):
            variant = candidate
            break

    # Where the light comes from, if the prompt says so.
    direction = "left" if "camera left" in text or "from the left" in text else "right"
    if "from above" in text or "overhead light" in text or "top light" in text:
        direction = "top"
    if "from behind" in text or "backlight" in text or "back-lit" in text:
        direction = "behind"

    return {
        "seed": seed,
        "rng": _rng(seed),
        "hue": hue,
        "saturation": saturation,
        "family": family,
        "variant": variant,
        "direction": direction,
        "is_video": str(preset.get("modality") or preset.get("kind") or "") == "video",
        "top": _hsl(hue, saturation * 0.7, 16),
        "bottom": _hsl(hue + 18, saturation * 0.8, 5),
        "key": _hsl(hue + 26, min(78, saturation + 26), 80),
        "rim": _hsl(hue - 58, min(74, saturation + 30), 66),
        "subject_hi": _hsl(hue + 14, saturation * 0.5, 17),
        "subject_mid": _hsl(hue + 4, saturation * 0.4, 9),
    }


def _defs(spec: Mapping[str, Any]) -> str:
    seed, top, bottom, key, rim = spec["seed"], spec["top"], spec["bottom"], spec["key"], spec["rim"]
    return f"""
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="0.62" stop-color="{bottom}"/>
      <stop offset="1" stop-color="#020203"/>
    </linearGradient>
    <radialGradient id="key" cx="0.5" cy="0.42" r="0.62">
      <stop offset="0" stop-color="{key}" stop-opacity="0.62"/>
      <stop offset="0.45" stop-color="{key}" stop-opacity="0.14"/>
      <stop offset="1" stop-color="{key}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="rimglow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{rim}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{rim}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="vig" cx="0.5" cy="0.48" r="0.74">
      <stop offset="0.5" stop-color="#000" stop-opacity="0"/>
      <stop offset="1" stop-color="#000" stop-opacity="0.7"/>
    </radialGradient>
    <linearGradient id="beam" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{key}" stop-opacity="0.4"/>
      <stop offset="1" stop-color="{key}" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="haze" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{rim}" stop-opacity="0"/>
      <stop offset="0.5" stop-color="{rim}" stop-opacity="0.26"/>
      <stop offset="1" stop-color="{rim}" stop-opacity="0"/>
    </linearGradient>
    <filter id="soft" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="20"/>
    </filter>
    <filter id="soft6" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="6"/>
    </filter>
    <filter id="grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="4" seed="{seed % 97}" stitchTiles="stitch"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.55" intercept="0"/></feComponentTransfer>
    </filter>
    <linearGradient id="subj" x1="0.1" y1="0" x2="0.8" y2="1">
      <stop offset="0" stop-color="{spec["subject_hi"]}"/>
      <stop offset="0.55" stop-color="{spec["subject_mid"]}"/>
      <stop offset="1" stop-color="#04050a"/>
    </linearGradient>
    <clipPath id="frame"><rect width="{WIDTH}" height="{HEIGHT}"/></clipPath>
  </defs>"""


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def _portrait(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    cx = WIDTH * (0.42 + rng() * 0.16)
    head_y = HEIGHT * (0.34 + rng() * 0.08)
    head_r = 58 + rng() * 20
    if spec["variant"] == "closeup":
        head_r *= 1.6
        head_y = HEIGHT * 0.44
    shoulder_y = head_y + head_r * 1.5
    parts = [
        f'<circle cx="{cx + (34 if spec["direction"] == "left" else -34):.0f}" cy="{head_y + 6:.0f}" r="{head_r * 2.4:.0f}" fill="url(#rimglow)" opacity="0.6"/>'
    ]
    if spec["variant"] == "two":
        for offset in (-head_r * 1.35, head_r * 1.35):
            parts.append(
                f'<ellipse cx="{cx + offset:.0f}" cy="{head_y + 14:.0f}" rx="{head_r * 0.68:.0f}" ry="{head_r * 0.8:.0f}" fill="url(#subj)"/>'
            )
            parts.append(
                f'<path d="M{cx + offset - head_r:.0f} {HEIGHT} C{cx + offset - head_r * 0.9:.0f} {shoulder_y + 50:.0f}, '
                f'{cx + offset - head_r * 0.4:.0f} {shoulder_y - 10:.0f}, {cx + offset:.0f} {shoulder_y - 18:.0f} '
                f'C{cx + offset + head_r * 0.4:.0f} {shoulder_y - 10:.0f}, {cx + offset + head_r * 0.9:.0f} {shoulder_y + 50:.0f}, '
                f'{cx + offset + head_r:.0f} {HEIGHT} Z" fill="url(#subj)"/>'
            )
        return "".join(parts)
    parts.append(
        f'<path d="M{cx - 178:.0f} {HEIGHT} C{cx - 158:.0f} {shoulder_y + 46:.0f}, '
        f'{cx - 82:.0f} {shoulder_y - 20:.0f}, {cx:.0f} {shoulder_y - 28:.0f} '
        f'C{cx + 82:.0f} {shoulder_y - 20:.0f}, {cx + 158:.0f} {shoulder_y + 46:.0f}, '
        f'{cx + 178:.0f} {HEIGHT} Z" fill="url(#subj)"/>'
    )
    parts.append(
        f'<rect x="{cx - 26:.0f}" y="{head_y + head_r * 0.52:.0f}" width="52" height="{head_r * 1.15:.0f}" fill="url(#subj)"/>'
    )
    parts.append(
        f'<ellipse cx="{cx:.0f}" cy="{head_y:.0f}" rx="{head_r * 0.8:.0f}" ry="{head_r:.0f}" fill="url(#subj)"/>'
    )
    # Rim on the light side, blurred so it reads as falloff rather than a line.
    side = -1 if spec["direction"] in ("left", "behind") else 1
    parts.append(
        f'<path d="M{cx + side * head_r * 0.74:.0f} {head_y - head_r * 0.5:.0f} '
        f'C{cx + side * head_r * 1.0:.0f} {head_y + head_r * 0.3:.0f}, {cx + side * head_r * 0.66:.0f} {head_y + head_r * 1.1:.0f}, '
        f'{cx + side * 16:.0f} {head_y + head_r * 1.28:.0f}" fill="none" stroke="{rim}" stroke-width="7" '
        f'stroke-linecap="round" opacity="0.9" filter="url(#soft6)"/>'
    )
    parts.append(
        f'<path d="M{cx + side * 150:.0f} {HEIGHT} C{cx + side * 140:.0f} {shoulder_y + 64:.0f}, '
        f'{cx + side * 92:.0f} {shoulder_y + 8:.0f}, {cx + side * 34:.0f} {shoulder_y - 4:.0f}" fill="none" '
        f'stroke="{key}" stroke-width="5" opacity="0.45" filter="url(#soft6)"/>'
    )
    return "".join(parts)


def _object(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    cx = WIDTH * (0.44 + rng() * 0.12)
    base_y = HEIGHT * (0.7 + rng() * 0.06)
    parts: List[str] = []
    if spec["variant"] == "topdown":
        # A flat-lay: objects laid on a surface, seen straight down.
        plate_r = 122 + rng() * 22
        parts.append(
            f'<circle cx="{cx:.0f}" cy="{HEIGHT * 0.5:.0f}" r="{plate_r:.0f}" fill="#0c0d10" stroke="{key}" stroke-opacity="0.28"/>'
        )
        parts.append(
            f'<circle cx="{cx:.0f}" cy="{HEIGHT * 0.5:.0f}" r="{plate_r - 16:.0f}" fill="none" stroke="{rim}" stroke-opacity="0.22"/>'
        )
        for _ in range(5):
            angle = rng() * 6.28
            radius = plate_r * (0.35 + rng() * 0.4)
            size = 16 + rng() * 26
            parts.append(
                f'<circle cx="{cx + radius * math.cos(angle):.0f}" '
                f'cy="{HEIGHT * 0.5 + radius * math.sin(angle):.0f}" '
                f'r="{size:.0f}" fill="#0a0b0d" stroke="{key}" stroke-opacity="0.16"/>'
            )
        return "".join(parts)
    if spec["variant"] == "bottle":
        w, h = 92, 250
        parts.append(
            f'<ellipse cx="{cx:.0f}" cy="{base_y + 10:.0f}" rx="{w * 1.4:.0f}" ry="22" fill="#000" opacity="0.6" filter="url(#soft)"/>'
        )
        parts.append(
            f'<path d="M{cx - w / 2:.0f} {base_y:.0f} L{cx - w / 2:.0f} {base_y - h * 0.66:.0f} '
            f'L{cx - 16:.0f} {base_y - h * 0.82:.0f} L{cx - 16:.0f} {base_y - h:.0f} L{cx + 16:.0f} {base_y - h:.0f} '
            f'L{cx + 16:.0f} {base_y - h * 0.82:.0f} L{cx + w / 2:.0f} {base_y - h * 0.66:.0f} L{cx + w / 2:.0f} {base_y:.0f} Z" fill="#0b0c0e"/>'
        )
        parts.append(
            f'<rect x="{cx - w / 2 + 10:.0f}" y="{base_y - h * 0.62:.0f}" width="{w * 0.2:.0f}" height="{h * 0.5:.0f}" rx="8" fill="{key}" opacity="0.2"/>'
        )
        parts.append(
            f'<path d="M{cx + w / 2 - 3:.0f} {base_y - h * 0.6:.0f} L{cx + w / 2 - 3:.0f} {base_y - 16:.0f}" stroke="{rim}" stroke-width="4" opacity="0.7" filter="url(#soft6)"/>'
        )
        return "".join(parts)
    w = 118 + rng() * 46
    h = 150 + rng() * 60
    parts = [
        f'<ellipse cx="{cx:.0f}" cy="{base_y + 12:.0f}" rx="{w * 1.5:.0f}" ry="26" fill="#000" opacity="0.55" filter="url(#soft)"/>',
        f'<rect x="{cx - w / 2:.0f}" y="{base_y - h:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{min(26, w / 4):.0f}" fill="url(#subj)"/>',
        f'<rect x="{cx - w / 2 + 8:.0f}" y="{base_y - h + 8:.0f}" width="{w * 0.26:.0f}" height="{h - 16:.0f}" rx="10" fill="{key}" opacity="0.16"/>',
        f'<ellipse cx="{cx:.0f}" cy="{base_y - h:.0f}" rx="{w / 2:.0f}" ry="{w / 6:.0f}" fill="{spec["subject_hi"]}"/>',
        f'<path d="M{cx + w / 2 - 4:.0f} {base_y - h + 18:.0f} L{cx + w / 2 - 4:.0f} {base_y - 18:.0f}" stroke="{rim}" stroke-width="4" opacity="0.65" filter="url(#soft6)"/>',
        f'<rect x="0" y="{base_y:.0f}" width="{WIDTH}" height="{HEIGHT - base_y:.0f}" fill="#050506" opacity="0.9"/>',
    ]
    return "".join(parts)


def _bokeh(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for _ in range(18):
        r = 8 + rng() * 44
        parts.append(
            f'<circle cx="{rng() * WIDTH:.0f}" cy="{rng() * HEIGHT:.0f}" r="{r:.0f}" '
            f'fill="{key if rng() > 0.5 else rim}" opacity="{0.06 + rng() * 0.18:.2f}" filter="url(#soft)"/>'
        )
    # One strand of focus in the middle: something the bokeh is behind.
    parts.append(
        f'<path d="M{WIDTH * 0.18:.0f} {HEIGHT * 0.62:.0f} C{WIDTH * 0.4:.0f} {HEIGHT * 0.5:.0f}, '
        f'{WIDTH * 0.62:.0f} {HEIGHT * 0.74:.0f}, {WIDTH * 0.86:.0f} {HEIGHT * 0.55:.0f}" stroke="{key}" '
        f'stroke-width="2" opacity="0.5" fill="none"/>'
    )
    return "".join(parts)


def _architecture(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    parts: List[str] = []
    if spec["variant"] == "arches":
        cols, rows = 4, 3
        for row in range(rows):
            for col in range(cols):
                x = 30 + col * ((WIDTH - 60) / cols)
                y = 60 + row * ((HEIGHT * 0.7) / rows)
                w = (WIDTH - 60) / cols - 18
                h = (HEIGHT * 0.7) / rows - 26
                parts.append(
                    f'<path d="M{x:.0f} {y + h:.0f} L{x:.0f} {y + h * 0.42:.0f} '
                    f'A{w / 2:.0f} {h * 0.42:.0f} 0 0 1 {x + w:.0f} {y + h * 0.42:.0f} L{x + w:.0f} {y + h:.0f} Z" '
                    f'fill="#0a0c0f" stroke="{key}" stroke-opacity="0.18"/>'
                )
                parts.append(
                    f'<path d="M{x + w * 0.5:.0f} {y + h:.0f} L{x + w * 0.5:.0f} {y + h * 0.55:.0f}" stroke="{rim}" stroke-opacity="0.2"/>'
                )
        return "".join(parts)
    if spec["variant"] == "corridor":
        for index in range(5):
            inset = index * 34
            parts.append(
                f'<rect x="{inset:.0f}" y="{inset * 0.8 + 40:.0f}" width="{WIDTH - inset * 2:.0f}" height="{HEIGHT - inset * 1.6 - 80:.0f}" '
                f'fill="none" stroke="{key}" stroke-opacity="{0.22 - index * 0.03:.2f}" stroke-width="2"/>'
            )
        parts.append(
            f'<polygon points="{WIDTH * 0.42:.0f},{HEIGHT * 0.32:.0f} {WIDTH * 0.58:.0f},{HEIGHT * 0.32:.0f} '
            f'{WIDTH * 0.62:.0f},{HEIGHT * 0.66:.0f} {WIDTH * 0.38:.0f},{HEIGHT * 0.66:.0f}" fill="{key}" opacity="0.28" filter="url(#soft)"/>'
        )
        return "".join(parts)
    if spec["variant"] == "stairs":
        for index in range(9):
            y = HEIGHT * 0.45 + index * 22
            x = 40 + index * 18
            parts.append(
                f'<rect x="{x:.0f}" y="{y:.0f}" width="{WIDTH - x - 30:.0f}" height="16" fill="#0a0c0f" stroke="{key}" stroke-opacity="0.12"/>'
            )
            parts.append(
                f'<rect x="{x:.0f}" y="{y:.0f}" width="{WIDTH - x - 30:.0f}" height="3" fill="{rim}" opacity="{max(0.04, 0.2 - index * 0.015):.2f}"/>'
            )
        parts.append(
            f'<polygon points="{WIDTH * 0.32:.0f},0 {WIDTH * 0.46:.0f},0 {WIDTH * 0.66:.0f},{HEIGHT} {WIDTH * 0.42:.0f},{HEIGHT}" fill="url(#beam)" opacity="0.5"/>'
        )
        return "".join(parts)
    # Plain slabs and a shaft of window light.
    for index in range(4):
        x = index * (WIDTH / 4) + rng() * 18
        w = 44 + rng() * 60
        top = 40 + rng() * 120
        parts.append(
            f'<rect x="{x:.0f}" y="{top:.0f}" width="{w:.0f}" height="{HEIGHT - top:.0f}" fill="#0a0c0f" opacity="{0.5 + rng() * 0.4:.2f}"/>'
        )
    for index in range(3):
        x = 60 + index * 140 + rng() * 40
        parts.append(
            f'<polygon points="{x:.0f},0 {x + 46:.0f},0 {x + 150:.0f},{HEIGHT} {x + 60:.0f},{HEIGHT}" '
            f'fill="url(#beam)" opacity="{0.35 + rng() * 0.3:.2f}"/>'
        )
    parts.append(f'<rect x="0" y="{HEIGHT * 0.72:.0f}" width="{WIDTH}" height="{HEIGHT * 0.28:.0f}" fill="#070809"/>')
    parts.append(
        f'<line x1="0" y1="{HEIGHT * 0.72:.0f}" x2="{WIDTH}" y2="{HEIGHT * 0.72:.0f}" stroke="{key}" stroke-width="1.2" opacity="0.24"/>'
    )
    return "".join(parts)


def _interior(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    """A room: one window, one pool of light, furniture in silhouette."""
    return "".join(
        [
            f'<rect x="{WIDTH * 0.08:.0f}" y="{HEIGHT * 0.14:.0f}" width="{WIDTH * 0.34:.0f}" height="{HEIGHT * 0.4:.0f}" fill="url(#beam)" opacity="0.85"/>',
            f'<rect x="{WIDTH * 0.08:.0f}" y="{HEIGHT * 0.14:.0f}" width="{WIDTH * 0.34:.0f}" height="{HEIGHT * 0.4:.0f}" fill="none" stroke="{key}" stroke-opacity="0.3"/>',
            f'<polygon points="{WIDTH * 0.08:.0f},{HEIGHT * 0.54:.0f} {WIDTH * 0.42:.0f},{HEIGHT * 0.54:.0f} {WIDTH * 0.62:.0f},{HEIGHT} {WIDTH * 0.16:.0f},{HEIGHT}" fill="{key}" opacity="0.1"/>',
            f'<rect x="0" y="{HEIGHT * 0.74:.0f}" width="{WIDTH}" height="{HEIGHT * 0.26:.0f}" fill="#070606"/>',
            f'<rect x="{WIDTH * 0.58:.0f}" y="{HEIGHT * 0.6:.0f}" width="{WIDTH * 0.3:.0f}" height="{HEIGHT * 0.16:.0f}" rx="10" fill="#0b0a09"/>',
            f'<rect x="{WIDTH * 0.6:.0f}" y="{HEIGHT * 0.56:.0f}" width="{WIDTH * 0.26:.0f}" height="{HEIGHT * 0.06:.0f}" rx="6" fill="#0e0c0a"/>',
            f'<circle cx="{WIDTH * 0.84:.0f}" cy="{HEIGHT * 0.42:.0f}" r="40" fill="{key}" opacity="0.35" filter="url(#soft)"/>',
            f'<rect x="{WIDTH * 0.835:.0f}" y="{HEIGHT * 0.42:.0f}" width="3" height="{HEIGHT * 0.16:.0f}" fill="{rim}" opacity="0.5"/>',
        ]
    )


def _landscape(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    horizon = HEIGHT * (0.6 + rng() * 0.08)
    sun_x = WIDTH * (0.28 + rng() * 0.42)
    parts = [
        f'<circle cx="{sun_x:.0f}" cy="{horizon - 40:.0f}" r="140" fill="url(#rimglow)" opacity="0.85"/>',
        f'<circle cx="{sun_x:.0f}" cy="{horizon - 46:.0f}" r="{24 + rng() * 14:.0f}" fill="{key}" opacity="0.8" filter="url(#soft6)"/>',
    ]
    for layer, (height, opacity, colour) in enumerate(
        ((0.2, 0.95, "#07090b"), (0.13, 0.8, "#0a0c0f"), (0.07, 0.65, "#0d1013"))
    ):
        points = [f"0,{horizon:.0f}"]
        x = 0
        while x < WIDTH:
            step = 70 + rng() * 60
            peak = horizon - HEIGHT * height * (0.3 + rng())
            points.append(f"{x + step / 2:.0f},{peak:.0f}")
            points.append(f"{x + step:.0f},{horizon - layer * 8:.0f}")
            x += step
        points.append(f"{WIDTH},{horizon:.0f}")
        parts.append(
            f'<polygon points="{" ".join(points)} {WIDTH},{HEIGHT} 0,{HEIGHT}" fill="{colour}" opacity="{opacity}"/>'
        )
    parts.append(f'<rect x="0" y="{horizon:.0f}" width="{WIDTH}" height="{HEIGHT - horizon:.0f}" fill="#050607"/>')
    parts.append(
        f'<rect x="0" y="{horizon - 28:.0f}" width="{WIDTH}" height="56" fill="url(#haze)" opacity="0.85"/>'
    )
    if spec["variant"] == "aerial":
        parts.append(
            f'<path d="M0 {HEIGHT * 0.8:.0f} C{WIDTH * 0.3:.0f} {HEIGHT * 0.68:.0f}, {WIDTH * 0.7:.0f} {HEIGHT * 0.9:.0f}, {WIDTH} {HEIGHT * 0.76:.0f}" stroke="{key}" stroke-width="2" stroke-opacity="0.4" fill="none"/>'
        )
    return "".join(parts)


def _urban(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    parts: List[str] = []
    street = HEIGHT * (0.66 + rng() * 0.05)
    x = 0
    while x < WIDTH:
        w = 50 + rng() * 70
        h = 90 + rng() * 220
        parts.append(f'<rect x="{x:.0f}" y="{street - h:.0f}" width="{w:.0f}" height="{h:.0f}" fill="#080a0d"/>')
        for row in range(int(h // 34)):
            for col in range(int(w // 26)):
                if rng() > 0.62:
                    parts.append(
                        f'<rect x="{x + 8 + col * 26:.0f}" y="{street - h + 12 + row * 34:.0f}" width="9" height="12" '
                        f'fill="{key}" opacity="{0.14 + rng() * 0.42:.2f}"/>'
                    )
        x += w + 6
    parts.append(f'<rect x="0" y="{street:.0f}" width="{WIDTH}" height="{HEIGHT - street:.0f}" fill="#040507"/>')
    for _ in range(6):
        rx = rng() * WIDTH
        parts.append(
            f'<rect x="{rx:.0f}" y="{street:.0f}" width="{12 + rng() * 34:.0f}" height="{HEIGHT - street:.0f}" '
            f'fill="{rim}" opacity="{0.06 + rng() * 0.12:.2f}" filter="url(#soft)"/>'
        )
    # A sign, vertical, bleeding its colour into the wet street.
    parts.append(
        f'<rect x="{WIDTH * 0.62:.0f}" y="{street * 0.34:.0f}" width="12" height="150" fill="{rim}" opacity="0.7" filter="url(#soft6)"/>'
    )
    parts.append(
        f'<rect x="{WIDTH * 0.62:.0f}" y="{street:.0f}" width="12" height="{HEIGHT - street:.0f}" fill="{rim}" opacity="0.22" filter="url(#soft)"/>'
    )
    if spec["variant"] == "silhouette":
        parts.append(
            f'<ellipse cx="{WIDTH * 0.34:.0f}" cy="{street * 0.78:.0f}" rx="20" ry="24" fill="#020304"/>'
            f'<path d="M{WIDTH * 0.34 - 40:.0f} {street:.0f} C{WIDTH * 0.34 - 34:.0f} {street * 0.86:.0f}, '
            f'{WIDTH * 0.34 - 16:.0f} {street * 0.83:.0f}, {WIDTH * 0.34:.0f} {street * 0.82:.0f} '
            f'C{WIDTH * 0.34 + 16:.0f} {street * 0.83:.0f}, {WIDTH * 0.34 + 34:.0f} {street * 0.86:.0f}, '
            f'{WIDTH * 0.34 + 40:.0f} {street:.0f} Z" fill="#020304"/>'
        )
    return "".join(parts)


def _automotive(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    road = HEIGHT * 0.78
    parts = [
        f'<rect x="0" y="{road:.0f}" width="{WIDTH}" height="{HEIGHT - road:.0f}" fill="#060708"/>',
        f'<rect x="0" y="{road - 150:.0f}" width="{WIDTH}" height="150" fill="url(#haze)" opacity="0.6"/>',
    ]
    for index in range(4):
        y = road - 40 - index * 26
        parts.append(
            f'<rect x="0" y="{y:.0f}" width="{WIDTH}" height="3" fill="{rim}" opacity="{0.1 + index * 0.06:.2f}" filter="url(#soft6)"/>'
        )
    parts.append(
        f'<path d="M60 {road - 10} C120 {road - 86}, 200 {road - 104}, 262 {road - 100} '
        f'L330 {road - 74} C386 {road - 62}, 420 {road - 44}, 430 {road - 12} Z" fill="#0a0b0d"/>'
    )
    parts.append(
        f'<circle cx="150" cy="{road - 8:.0f}" r="22" fill="#050607" stroke="{key}" stroke-opacity="0.35"/>'
    )
    parts.append(
        f'<circle cx="360" cy="{road - 8:.0f}" r="22" fill="#050607" stroke="{key}" stroke-opacity="0.35"/>'
    )
    parts.append(f'<rect x="112" y="{road - 82:.0f}" width="180" height="4" fill="{key}" opacity="0.55" filter="url(#soft6)"/>')
    return "".join(parts)


def _graphic(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    parts: List[str] = []
    shape = int(rng() * 3)
    if shape == 0:
        parts.append(f'<circle cx="{WIDTH * 0.5:.0f}" cy="{HEIGHT * 0.44:.0f}" r="{150 + rng() * 30:.0f}" fill="{rim}" opacity="0.85"/>')
        parts.append(f'<rect x="0" y="{HEIGHT * 0.62:.0f}" width="{WIDTH}" height="16" fill="{key}" opacity="0.7"/>')
    elif shape == 1:
        parts.append(
            f'<polygon points="{WIDTH * 0.5:.0f},110 {WIDTH - 80:.0f},{HEIGHT - 120:.0f} 80,{HEIGHT - 120:.0f}" fill="{rim}" opacity="0.8"/>'
        )
        parts.append(f'<circle cx="{WIDTH * 0.5:.0f}" cy="{HEIGHT * 0.62:.0f}" r="54" fill="#050506" opacity="0.9"/>')
    else:
        for index in range(4):
            parts.append(
                f'<rect x="{30 + index * 104:.0f}" y="{120 + (index % 2) * 110:.0f}" width="86" height="{200 + index * 26}" '
                f'fill="{rim if index % 2 == 0 else key}" opacity="{0.5 + index * 0.1:.2f}"/>'
            )
    return "".join(parts)


def _abstract(rng, key: str, rim: str, spec: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for index in range(6):
        parts.append(
            f'<ellipse cx="{rng() * WIDTH:.0f}" cy="{rng() * HEIGHT:.0f}" rx="{90 + rng() * 200:.0f}" '
            f'ry="{60 + rng() * 140:.0f}" fill="{key if index % 2 else rim}" opacity="{0.07 + rng() * 0.13:.2f}" '
            f'filter="url(#soft)" transform="rotate({rng() * 60 - 30:.0f})"/>'
        )
    for _ in range(4):
        y = rng() * HEIGHT
        parts.append(
            f'<path d="M0 {y:.0f} C{WIDTH * 0.3:.0f} {y - 80:.0f}, {WIDTH * 0.7:.0f} {y + 80:.0f}, {WIDTH} {y - 20:.0f}" '
            f'stroke="{key}" stroke-width="{1 + rng() * 2:.1f}" fill="none" opacity="0.4"/>'
        )
    return "".join(parts)


SCENES = {
    "portrait": _portrait,
    "object": _object,
    "bokeh": _bokeh,
    "architecture": _architecture,
    "interior": _interior,
    "landscape": _landscape,
    "urban": _urban,
    "automotive": _automotive,
    "graphic": _graphic,
    "abstract": _abstract,
}


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

_VARIANT_TO_FAMILY = {
    "topdown": "object",
    "stairs": "architecture",
    "corridor": "architecture",
    "arches": "architecture",
    "closeup": "portrait",
    "two": "portrait",
    "bottle": "object",
    "ridges": "landscape",
    "aerial": "landscape",
    "silhouette": "urban",
}


def family_for(preset: Mapping[str, Any]) -> str:
    """The composition family, refined by the preset's own framing words."""
    spec = _spec(preset)
    return _VARIANT_TO_FAMILY.get(spec["variant"], spec["family"])


def card_svg(preset: Mapping[str, Any]) -> str:
    """One style card as an SVG document, deterministic for the preset's key."""
    spec = _spec(preset)
    family = family_for(preset)
    scene = SCENES[family](spec["rng"], spec["key"], spec["rim"], spec)
    bar = 26 if spec["is_video"] else 10
    third = HEIGHT / 3
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="Style card generated from this preset's tokens">
{_defs(spec)}
  <g clip-path="url(#frame)">
    <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>
    <circle cx="{WIDTH * 0.5:.0f}" cy="{HEIGHT * 0.4:.0f}" r="{WIDTH * 0.66:.0f}" fill="url(#key)"/>
    {scene}
    <rect x="0" y="0" width="{WIDTH}" height="{bar}" fill="#000" opacity="0.92"/>
    <rect x="0" y="{HEIGHT - bar}" width="{WIDTH}" height="{bar}" fill="#000" opacity="0.92"/>
    <rect width="{WIDTH}" height="{HEIGHT}" filter="url(#grain)" opacity="0.14"/>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#vig)"/>
    <g opacity="0.26" stroke="#ffffff" stroke-width="0.5" fill="none">
      <line x1="{WIDTH * 0.12:.0f}" y1="{third:.0f}" x2="{WIDTH * 0.12:.0f}" y2="{third + 14:.0f}"/>
      <line x1="0" y1="{third:.0f}" x2="16" y2="{third:.0f}"/>
    </g>
  </g>
</svg>
"""


def card_path(preset: Mapping[str, Any]) -> str:
    """Where the card is served from, for a preset the API just returned."""
    key = str(preset.get("key") or preset.get("slug") or preset.get("id") or "")
    return "/api/preset-cards/%s.svg" % key
