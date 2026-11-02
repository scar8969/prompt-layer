"""The Director's Panel: craft vocabulary compiled into one prompt.

A Higgsfield-style panel is a list of choices — camera body, lens, focal length,
aperture, light, palette, mood, movement — that all end up as words in the same
sentence. This module is that sentence, and nothing else: it holds the
vocabulary, the exact phrase each option contributes, and a compiler that
returns the assembled prompt **split into the segments that produced it**, so
the interface can show which clause came from which control.

Two rules keep it honest:

* Every option carries the phrase it adds. The compiler never paraphrases,
  never calls a model, and never fills an empty control with a guess — leave a
  control unset and its clause is absent, with a note saying so.
* The vocabulary is craft, not provider behaviour. These are the descriptors a
  director and a cinematographer actually use; they are not claims about what a
  given endpoint does to a prompt. Provider behaviour lives in
  ``promptlayer/layers.json`` and is shown next to this, never mixed into it.

Pure data and pure functions: no sockets, no files, no environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .adapters.base import CAMERA_MOVES_BY_KEY
from .errors import VALIDATION_FAILED, ApiError

# ---------------------------------------------------------------------------
# Option shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    """One choice on the panel, and the words it contributes."""

    key: str
    label: str
    adds: str
    note: str = ""
    #: Set on options that also map to a provider parameter.
    parameter: str = ""
    value: Any = None


def _group(options: Iterable[Option]) -> Tuple[Option, ...]:
    return tuple(options)


# ---------------------------------------------------------------------------
# Camera bodies
# ---------------------------------------------------------------------------

BODIES: Tuple[Option, ...] = _group(
    [
        Option(
            "arri_alexa_35",
            "ARRI Alexa 35",
            "shot on an ARRI Alexa 35",
            "Super 35 sensor; the reference for skin tone and highlight roll-off.",
        ),
        Option(
            "arri_alexa_mini_lf",
            "ARRI Alexa Mini LF",
            "shot on an ARRI Alexa Mini LF",
            "Large format, shallow field, the current default for premium commercials.",
        ),
        Option(
            "red_v_raptor",
            "RED V-Raptor",
            "shot on a RED V-Raptor",
            "High-contrast digital look with crisp edges and saturated reds.",
        ),
        Option(
            "sony_venice_2",
            "Sony VENICE 2",
            "shot on a Sony VENICE 2",
            "Dual-base ISO; clean shadows and a slightly cool neutral.",
        ),
        Option(
            "panavision_dxl2",
            "Panavision DXL2",
            "shot on a Panavision DXL2",
            "Large format with the gentle, slightly warm Panavision colour science.",
        ),
        Option(
            "phantom_flex",
            "Phantom Flex",
            "shot on a Phantom Flex at high frame rate",
            "High-speed body; motion is stretched rather than frozen.",
        ),
        Option(
            "bolex_16",
            "Bolex 16 mm",
            "shot on 16 mm film with a Bolex",
            "Coarse grain, gate weave, soft corners — an archival texture.",
        ),
        Option(
            "phone",
            "Phone camera",
            "shot on a phone camera, vertical framing",
            "Wide lens, deep focus, computational contrast: the native social look.",
        ),
        Option(
            "none",
            "No body named",
            "",
            "The prompt says nothing about the camera; the model picks its own.",
        ),
    ]
)

# ---------------------------------------------------------------------------
# Lenses and optics
# ---------------------------------------------------------------------------

LENSES: Tuple[Option, ...] = _group(
    [
        Option(
            "cooke_s4",
            "Cooke S4",
            "Cooke S4 primes",
            "The 'Cooke look': warm skin, gentle micro-contrast, no clinical bite.",
        ),
        Option(
            "zeiss_supreme",
            "Zeiss Supreme Prime",
            "Zeiss Supreme Primes",
            "Clean, high micro-contrast, neutral colour and a sharp falloff.",
        ),
        Option(
            "panavision_c",
            "Panavision C-series anamorphic",
            "Panavision C-series anamorphic",
            "Oval bokeh, horizontal flares, slight barrel distortion at the edges.",
        ),
        Option(
            "leica_summicron",
            "Leica Summicron-C",
            "Leica Summicron-C primes",
            "Compact, contrasty, a modern documentary edge.",
        ),
        Option(
            "helios_44",
            "Helios 44-2 (vintage)",
            "a vintage Helios 44-2",
            "Swirly bokeh and low contrast; the look of a Soviet 58 mm.",
        ),
        Option(
            "macro_100",
            "100 mm macro",
            "a 100 mm macro lens",
            "Flat field, 1:1 magnification, texture resolved at close range.",
        ),
        Option(
            "tilt_shift",
            "Tilt-shift",
            "a tilt-shift lens",
            "A plane of focus that does not follow perspective — miniatures, architecture.",
        ),
        Option(
            "none",
            "No lens named",
            "",
            "Lens character is left to the model.",
        ),
    ]
)

FOCAL_LENGTHS: Tuple[Option, ...] = _group(
    [
        Option("14", "14 mm", "14 mm", "Extreme wide; edges stretch and space opens up."),
        Option("18", "18 mm", "18 mm", "Wide; strong perspective, big foreground."),
        Option("24", "24 mm", "24 mm", "Wide reportage framing with visible context."),
        Option("35", "35 mm", "35 mm", "The documentary default: close to human attention."),
        Option("50", "50 mm", "50 mm", "Neutral perspective, minimal distortion."),
        Option("85", "85 mm", "85 mm", "The portrait length; background compresses and softens."),
        Option("135", "135 mm", "135 mm", "Strong compression, isolated subject."),
        Option("200", "200 mm", "200 mm", "Distant and flattened; foreground disappears."),
    ]
)

APERTURES: Tuple[Option, ...] = _group(
    [
        Option("1.2", "f/1.2", "at f/1.2", "Paper-thin focus; only the eyes survive."),
        Option("1.4", "f/1.4", "at f/1.4", "Very shallow, a soft and creamy falloff."),
        Option("1.8", "f/1.8", "at f/1.8", "Shallow but forgiving; the portrait standard."),
        Option("2.8", "f/2.8", "at f/2.8", "Subject separated, environment still readable."),
        Option("4", "f/4", "at f/4", "Balanced: the face is sharp, the room is suggested."),
        Option("5.6", "f/5.6", "at f/5.6", "Deep enough for a group or a product's edges."),
        Option("8", "f/8", "at f/8", "Everything in the plane is sharp; no focus anxiety."),
        Option("11", "f/11", "at f/11", "Landscape depth, at the cost of diffraction softness."),
        Option("16", "f/16", "at f/16", "Maximum depth; light must be plentiful."),
    ]
)

SHUTTERS: Tuple[Option, ...] = _group(
    [
        Option("45", "45° (crisp)", "45-degree shutter", "Staccato motion, private-investigator crispness."),
        Option("90", "90° (clean)", "90-degree shutter", "Reduced motion blur, a modern action feel."),
        Option("180", "180° (natural)", "180-degree shutter", "The filmic default: natural motion blur."),
        Option("360", "360° (smeared)", "360-degree shutter", "Heavy blur, dreamlike and continuous."),
    ]
)

# ---------------------------------------------------------------------------
# Light
# ---------------------------------------------------------------------------

LIGHTING: Tuple[Option, ...] = _group(
    [
        Option(
            "rembrandt",
            "Rembrandt key",
            "a single Rembrandt key at 45 degrees camera left, triangle of light on the far cheek",
            "One source, one shadow, the classic portrait triangle.",
        ),
        Option(
            "split",
            "Split light",
            "hard split lighting, exactly half the face in shadow",
            "Drama with no fill; the centre line is the story.",
        ),
        Option(
            "butterfly",
            "Butterfly / paramount",
            "butterfly lighting from directly above the lens, a small shadow under the nose",
            "Beauty-standard light; flatters, and shows cheekbones.",
        ),
        Option(
            "hard_flash",
            "Direct hard flash",
            "direct on-camera flash, hard shadow thrown behind the subject",
            "Snapshot immediacy; the shadow becomes part of the composition.",
        ),
        Option(
            "golden_hour",
            "Golden hour",
            "low golden-hour sun raking from camera right, long shadows across the ground",
            "Warm, motivated, and short-lived — the light everyone waits for.",
        ),
        Option(
            "blue_hour",
            "Blue hour",
            "blue-hour ambience with warm practicals starting to glow",
            "Cool ambient plus warm sources: the city-at-dusk contrast.",
        ),
        Option(
            "overcast",
            "Overcast",
            "flat overcast light, no visible shadow edge",
            "A giant softbox in the sky; colour and texture carry the frame.",
        ),
        Option(
            "neon_practicals",
            "Neon practicals",
            "neon practicals and signage spilling magenta and cyan onto the subject",
            "Colour comes from inside the world, not from a gel on the light.",
        ),
        Option(
            "firelight",
            "Firelight",
            "warm flickering firelight from below frame, unsteady and orange",
            "Motivated warmth with movement in the light itself.",
        ),
        Option(
            "single_softbox",
            "Single softbox",
            "one large softbox directly overhead at forty-five degrees, white card fill from camera left",
            "The catalogue standard: even, controllable, repeatable.",
        ),
        Option(
            "window_light",
            "Window light",
            "soft north-facing window light from camera left, curtain edge just inside frame",
            "The cheapest good light there is; direction reads as a room.",
        ),
        Option(
            "top_light",
            "Top light",
            "a hard top light, eye sockets in shadow",
            "Interrogation, unease, or a stage spotlight depending on intensity.",
        ),
        Option(
            "moonlight",
            "Moonlight",
            "cold blue moonlight from a high angle, deep shadow with no fill",
            "Night exterior convention: blue, directional, under-exposed.",
        ),
        Option(
            "screen_glow",
            "Screen glow",
            "the only light is a screen's glow on the face, cool and close",
            "Modern isolation; the source is inside the frame's logic.",
        ),
    ]
)

# ---------------------------------------------------------------------------
# Colour and finish
# ---------------------------------------------------------------------------

PALETTES: Tuple[Option, ...] = _group(
    [
        Option(
            "teal_orange",
            "Teal and orange",
            "teal shadows against warm orange skin, the modern blockbuster grade",
            "Complementary separation of subject from background.",
        ),
        Option(
            "kodak_2383",
            "Kodak 2383 print",
            "graded through a Kodak 2383 print emulation, rich blacks and gentle highlights",
            "The print stock most digital cinema is finished through.",
        ),
        Option(
            "bleach_bypass",
            "Bleach bypass",
            "bleach-bypass grade, desaturated with crushed contrast",
            "Silver retained: muted colour and metallic highlights.",
        ),
        Option(
            "monochrome",
            "Monochrome",
            "black and white, deep blacks with a wide tonal range",
            "Removes colour as a crutch; form and light only.",
        ),
        Option(
            "pastel",
            "Pastel",
            "soft pastel palette, low contrast, milk highlights",
            "Editorial softness; bright but never clipped.",
        ),
        Option(
            "neon",
            "Neon night",
            "saturated magenta and cyan against black, wet reflections",
            "Colour as the subject, blacks as the canvas.",
        ),
        Option(
            "earthy",
            "Earthy naturals",
            "earthy naturals — clay, olive, bone, rust — matte finish",
            "Texture-forward, low saturation, no gloss.",
        ),
        Option(
            "silver",
            "Silver halide",
            "cool silver highlights, low saturation, metallic sheen",
            "Analogue metal: colder than neutral, never blue.",
        ),
        Option(
            "cross_process",
            "Cross process",
            "cross-processed colour, green shadows and yellow highlights",
            "Deliberately wrong chemistry, as a style.",
        ),
    ]
)

GRAIN: Tuple[Option, ...] = _group(
    [
        Option("none", "Clean", "clean digital image, no grain", "Modern capture, no texture added."),
        Option("fine", "Fine grain", "fine 35 mm grain", "Texture you feel more than see."),
        Option("medium", "Medium grain", "visible 35 mm grain in the shadows", "Film texture, still sharp."),
        Option("heavy", "Heavy grain", "coarse grain across the whole frame", "Texture as a subject."),
    ]
)

# ---------------------------------------------------------------------------
# Framing, mood, motion
# ---------------------------------------------------------------------------

SHOTS: Tuple[Option, ...] = _group(
    [
        Option("extreme_wide", "Extreme wide", "extreme wide shot", "Scale and isolation."),
        Option("wide", "Wide", "wide shot", "Subject in context."),
        Option("full", "Full", "full-length shot", "Body language and costume."),
        Option("medium", "Medium", "medium shot from the waist up", "The default for dialogue."),
        Option("medium_close", "Medium close", "medium close-up from the chest up", "Performance and hands."),
        Option("close", "Close-up", "close-up", "Expression; the background becomes colour."),
        Option("extreme_close", "Extreme close", "extreme close-up on a detail", "Texture and implication."),
        Option("insert", "Insert", "macro insert shot", "A detail that carries the story."),
    ]
)

ANGLES: Tuple[Option, ...] = _group(
    [
        Option("eye", "Eye level", "at eye level", "Neutral, observational."),
        Option("low", "Low angle", "from a low angle looking up", "Power and threat."),
        Option("high", "High angle", "from a high angle looking down", "Vulnerability and geometry."),
        Option("dutch", "Dutch tilt", "with the horizon tilted a few degrees", "Instability, unease."),
        Option("overhead", "Overhead", "straight down from overhead", "Graphic, map-like composition."),
        Option("pov", "POV", "from the subject's point of view", "The viewer becomes the character."),
    ]
)

COMPOSITIONS: Tuple[Option, ...] = _group(
    [
        Option("centered", "Centred", "subject centred with symmetrical margins", "Formal and graphic."),
        Option("thirds", "Rule of thirds", "subject on the left third, looking into frame", "Room to move; the eye follows."),
        Option("negative_space", "Negative space", "subject small in a wide field of negative space", "Isolation, scale, or a title's home."),
        Option("frame_in_frame", "Frame in frame", "framed by a doorway inside the image", "Depth and enclosure."),
        Option("foreground", "Foreground layers", "a soft foreground element at the frame edge", "Depth without a wide lens."),
    ]
)

MOODS: Tuple[Option, ...] = _group(
    [
        Option("calm", "Calm", "a calm, unhurried performance", "Stillness reads as confidence."),
        Option("tense", "Tense", "held tension, jaw set, no release", "The pause before something."),
        Option("joy", "Joy", "open, unforced joy", "Light in the face, not a grin."),
        Option("grief", "Grief", "restrained grief held behind the eyes", "Emotion contained is emotion seen."),
        Option("menace", "Menace", "quiet menace, unblinking", "Threat without movement."),
        Option("wonder", "Wonder", "wide-eyed wonder, looking up", "Scale through reaction."),
        Option("exhaustion", "Exhaustion", "the particular tiredness of a long day", "Weight in the shoulders."),
        Option("confidence", "Confidence", "easy confidence, weight on the back foot", "Grounded, not posed."),
        Option("longing", "Longing", "longing, looking just past the lens", "The space between people."),
        Option("neutral", "Neutral", "a neutral, unposed expression", "The subject is not performing."),
    ]
)

MOVEMENTS: Tuple[str, ...] = (
    "static",
    "dolly_in",
    "dolly_out",
    "push_in",
    "pull_out",
    "tracking",
    "orbit",
    "pan",
    "tilt",
    "crane_up",
    "crane_down",
    "handheld",
    "whip_pan",
    "fpv",
    "aerial",
    "slow_motion",
    "rack_focus",
)

#: Aspects the panel offers, with the reason each is used.
ASPECTS: Tuple[Option, ...] = _group(
    [
        Option("2.39:1", "2.39:1 Anamorphic", "2.39:1 anamorphic frame", "Scope; landscape and scale."),
        Option("16:9", "16:9", "16:9 frame", "Broadcast and platform default."),
        Option("4:3", "4:3", "4:3 frame", "Archive, television, and the head-and-shoulders portrait."),
        Option("1:1", "1:1", "1:1 square frame", "Feed-native; the object fills the frame."),
        Option("4:5", "4:5", "4:5 portrait frame", "The editorial portrait crop."),
        Option("9:16", "9:16", "9:16 vertical frame", "Full-bleed phone."),
        Option("3:2", "3:2", "3:2 frame", "The stills default."),
    ]
)

#: The studio's baseline negative. Editable, and said to be a convention rather
#: than a fact about any provider.
DEFAULT_NEGATIVE = (
    "text overlays, watermarks, signature, logo, extra fingers, warped hands, "
    "duplicated limbs, plastic skin, oversharpened, blown highlights"
)

SEGMENT_ORDER: Tuple[Tuple[str, str], ...] = (
    ("subject", "Subject"),
    ("setting", "Setting"),
    ("framing", "Framing"),
    ("camera", "Camera"),
    ("exposure", "Exposure"),
    ("light", "Light"),
    ("colour", "Colour"),
    ("mood", "Performance"),
    ("motion", "Movement"),
    ("finish", "Finish"),
)

#: Keys the panel accepts, grouped the way the panel is laid out.
VOCABULARY: Dict[str, Tuple[Option, ...]] = {
    "bodies": BODIES,
    "lenses": LENSES,
    "focal_lengths": FOCAL_LENGTHS,
    "apertures": APERTURES,
    "shutters": SHUTTERS,
    "lighting": LIGHTING,
    "palettes": PALETTES,
    "grain": GRAIN,
    "shots": SHOTS,
    "angles": ANGLES,
    "compositions": COMPOSITIONS,
    "moods": MOODS,
    "aspects": ASPECTS,
}

_BY_KEY: Dict[str, Dict[str, Option]] = {
    name: {option.key: option for option in options} for name, options in VOCABULARY.items()
}


def option(group: str, key: str) -> Optional[Option]:
    """One option by group and key, or None. An unknown key never raises."""
    return _BY_KEY.get(group, {}).get(key)


#: How the panel is laid out: a group, the controls inside it, and the option
#: list each control draws from. A control with no list is free text.
PANEL: Tuple[Tuple[str, str, str, Tuple[Tuple[str, str, str], ...]], ...] = (
    (
        "subject",
        "Subject",
        "What is in the frame. Free text — the panel only frames it, it never invents one.",
        (
            ("subject", "Subject", ""),
            ("setting", "Setting", ""),
        ),
    ),
    (
        "framing",
        "Framing",
        "Where the camera stands: shot size, angle, composition.",
        (
            ("shots", "Shot size", "shots"),
            ("angles", "Angle", "angles"),
            ("compositions", "Composition", "compositions"),
        ),
    ),
    (
        "camera",
        "Camera",
        "Body and glass — the character of the capture.",
        (
            ("bodies", "Camera body", "bodies"),
            ("lenses", "Lens", "lenses"),
        ),
    ),
    (
        "exposure",
        "Exposure",
        "Focal length, aperture, shutter. What is sharp and what moves.",
        (
            ("focal_lengths", "Focal length", "focal_lengths"),
            ("apertures", "Aperture", "apertures"),
            ("shutters", "Shutter", "shutters"),
        ),
    ),
    (
        "light",
        "Light",
        "One setup. Where the story is lit from.",
        (("lighting", "Lighting", "lighting"),),
    ),
    (
        "colour",
        "Colour",
        "Palette and the stock it is finished through.",
        (("palettes", "Palette", "palettes"),),
    ),
    (
        "mood",
        "Performance",
        "What the subject is doing with their face and body.",
        (("moods", "Performance", "moods"),),
    ),
    (
        "motion",
        "Movement",
        "Camera movement. A still image ignores this control.",
        (("movement", "Move", "movements"),),
    ),
    (
        "finish",
        "Finish",
        "Grain and texture.",
        (("grain", "Grain", "grain"),),
    ),
    (
        "frame",
        "Frame",
        "Aspect ratio, and the negative prompt that goes with every request.",
        (("aspects", "Aspect ratio", "aspects"),),
    ),
)


def _option_document(item: Option) -> Dict[str, Any]:
    return {"key": item.key, "label": item.label, "adds": item.adds, "note": item.note}


def vocabulary_document() -> Dict[str, Any]:
    """The whole panel as JSON: groups in layout order, each with its controls."""
    groups: List[Dict[str, Any]] = []
    for key, label, summary, fields in PANEL:
        control_docs: List[Dict[str, Any]] = []
        for name, field_label, options_key in fields:
            if options_key == "movements":
                options = [
                    {
                        "key": move.key,
                        "label": move.label,
                        "adds": move.phrase,
                        "note": "Provider field '%s'." % move.parameter
                        if move.parameter
                        else "Carried in the prompt: this endpoint declares no camera field.",
                    }
                    for move in (CAMERA_MOVES_BY_KEY[item] for item in MOVEMENTS)
                    if move is not None
                ]
            elif options_key:
                options = [_option_document(item) for item in VOCABULARY.get(options_key, ())]
            else:
                options = []
            control_docs.append(
                {
                    "name": name,
                    "label": field_label,
                    "kind": "choice" if options else "text",
                    "options": options,
                }
            )
        groups.append(
            {"key": key, "label": label, "summary": summary, "controls": control_docs}
        )
    return {
        "groups": groups,
        "default_negative": DEFAULT_NEGATIVE,
        "segment_order": [{"key": key, "label": label} for key, label in SEGMENT_ORDER],
        "option_count": sum(
            len(options) for options in VOCABULARY.values()
        )
        + len(MOVEMENTS),
    }


# ---------------------------------------------------------------------------
# The compiler
# ---------------------------------------------------------------------------


def _pick(brief: Mapping[str, Any], group: str) -> Optional[Option]:
    key = str(brief.get(group) or "").strip()
    if not key:
        return None
    return option(group, key)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _join(parts: Sequence[str]) -> str:
    """Join clauses into one sentence: commas, an 'and' before the last."""
    items = [item.strip().strip(",") for item in parts if item and item.strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def validate_brief(brief: Mapping[str, Any]) -> None:
    """Refuse a choice outside the panel's vocabulary, by name.

    One implementation, called by the HTTP route and the MCP tool. A compiler
    that silently ignored an unknown option would produce a prompt missing a
    clause the caller believes it sent -- and the ledger would look complete.
    """
    for group in (
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
        "aspects",
    ):
        value = _clean(brief.get(group))
        if value and option(group, value) is None:
            raise ApiError(
                VALIDATION_FAILED,
                "Unknown option '%s' for %s." % (value, group),
                field=group,
            )
    movement = _clean(brief.get("movement"))
    if movement and movement not in set(MOVEMENTS):
        raise ApiError(VALIDATION_FAILED, "Unknown camera move '%s'." % movement, field="movement")
    modality = _clean(brief.get("modality")) or "image"
    if modality not in ("image", "video"):
        raise ApiError(
            VALIDATION_FAILED, "'modality' must be 'image' or 'video'.", field="modality"
        )


def compile_brief(brief: Mapping[str, Any]) -> Dict[str, Any]:
    """Assemble one prompt from the panel's choices.

    Every returned segment names the control that produced it and the exact
    text it contributed, so the client can show an injection ledger rather than
    one opaque string. A control that is unset contributes nothing and is
    reported in ``omitted``.

    ``disabled`` lists segment keys the caller has switched off; a disabled
    segment still appears in ``segments`` with ``enabled: false`` and its text,
    because seeing what you removed is the point of the ledger.
    """
    disabled = {str(item) for item in (brief.get("disabled") or [])}
    modality = str(brief.get("modality") or "image")

    subject = _clean(brief.get("subject"))
    setting = _clean(brief.get("setting"))

    shot = _pick(brief, "shots")
    angle = _pick(brief, "angles")
    composition = _pick(brief, "compositions")
    body = _pick(brief, "bodies")
    lens = _pick(brief, "lenses")
    focal = _pick(brief, "focal_lengths")
    aperture = _pick(brief, "apertures")
    shutter = _pick(brief, "shutters")
    light = _pick(brief, "lighting")
    palette = _pick(brief, "palettes")
    grain = _pick(brief, "grain")
    mood = _pick(brief, "moods")
    aspect = _pick(brief, "aspects")

    movement_key = str(brief.get("movement") or "").strip()
    movement = CAMERA_MOVES_BY_KEY.get(movement_key)

    framing_parts = [part.adds for part in (shot, angle, composition) if part and part.adds]
    camera_text = ""
    if body and body.adds:
        camera_text = body.adds
        if lens and lens.adds:
            camera_text += ", " + lens.adds
    elif lens and lens.adds:
        camera_text = "shot with " + lens.adds
    exposure_text = ""
    if focal and focal.adds:
        exposure_text = focal.adds
        if aperture and aperture.adds:
            exposure_text += " " + aperture.adds
    elif aperture and aperture.adds:
        exposure_text = aperture.adds.lstrip("at ").rstrip()
        exposure_text = "aperture " + exposure_text
    if shutter and shutter.adds:
        exposure_text = (exposure_text + ", " + shutter.adds).strip(", ")

    segments: List[Dict[str, Any]] = []

    def add(key: str, label: str, text: str, source: str) -> None:
        if not text:
            return
        segments.append(
            {
                "key": key,
                "label": label,
                "text": text,
                "source": source,
                "enabled": key not in disabled,
            }
        )

    add("subject", "Subject", subject, "you")
    add("setting", "Setting", setting, "you")
    add("framing", "Framing", _join(framing_parts), "framing")
    add("camera", "Camera", camera_text, "camera")
    add("exposure", "Exposure", exposure_text, "exposure")
    add("light", "Light", light.adds if light else "", "light")
    add("colour", "Colour", palette.adds if palette else "", "colour")
    add("mood", "Performance", mood.adds if mood else "", "mood")
    add(
        "motion",
        "Movement",
        movement.phrase if (movement and modality == "video") else "",
        "motion",
    )
    add("finish", "Finish", grain.adds if grain else "", "finish")

    prompt = _join([segment["text"] for segment in segments if segment["enabled"]])
    if prompt:
        prompt = prompt[0].upper() + prompt[1:]
        prompt = prompt.rstrip(".") + "."

    negative = _clean(brief.get("negative")) or DEFAULT_NEGATIVE

    omitted = [
        label
        for key, label in SEGMENT_ORDER
        if not any(segment["key"] == key and segment["enabled"] for segment in segments)
    ]

    parameters: Dict[str, Any] = {}
    if aspect:
        parameters["aspect_ratio"] = aspect.key
    if movement_key and modality == "video":
        parameters["camera_move"] = movement_key
    if aperture:
        # A string, because f-numbers are labels, not arithmetic inputs.
        parameters["aperture"] = "f/%s" % aperture.key

    warnings: List[Dict[str, str]] = []
    if focal and aperture and int(focal.key) >= 135 and float(aperture.key) <= 1.8:
        warnings.append(
            {
                "level": "craft",
                "text": "%s at f/%s: the field is a few centimetres deep. Expect the ears and "
                "the background to dissolve; keep the eyes on the focus plane."
                % (focal.key, aperture.key),
            }
        )
    if lens and lens.key == "panavision_c" and focal and int(focal.key) <= 18:
        warnings.append(
            {
                "level": "craft",
                "text": "Anamorphic at %s mm bends verticals near the frame edge. Useful for "
                "unease; wrong for architecture that must stay straight." % focal.key,
            }
        )
    if movement_key and shot and shot.key in ("extreme_close", "insert") and movement_key not in (
        "static",
        "slow_motion",
        "rack_focus",
    ):
        warnings.append(
            {
                "level": "craft",
                "text": "A %s on an %s is hard to hold: the subject leaves the frame before the "
                "move reads." % (movement.label.lower(), shot.label.lower()),
            }
        )
    if not subject:
        warnings.append(
            {
                "level": "missing",
                "text": "No subject. The panel will not invent one — the step after this is a "
                "model's own idea of what to draw.",
            }
        )
    if palette and palette.key == "monochrome" and light and light.key == "neon_practicals":
        warnings.append(
            {
                "level": "craft",
                "text": "Neon practicals graded monochrome keeps only their brightness. If the "
                "colour is the point, leave the palette off.",
            }
        )

    return {
        "prompt": prompt,
        "negative_prompt": negative,
        "segments": segments,
        "omitted": omitted,
        "parameters": parameters,
        "warnings": warnings,
        "modality": modality,
        "aspect_ratio": aspect.key if aspect else "",
        "storyboard_hint": _storyboard_hint(segments, movement),
    }


def _storyboard_hint(segments: Sequence[Mapping[str, Any]], movement: Any) -> str:
    """One line describing the shot, for a storyboard card title."""
    pieces = []
    for segment in segments:
        key = segment["key"]
        text = str(segment["text"])
        if key == "framing" and text:
            pieces.append(text.split(",")[0])
        elif key == "subject" and text:
            pieces.append(text[:64])
    if movement is not None:
        pieces.append(movement.label)
    return " · ".join(pieces[:3]) if pieces else "Untitled shot"
