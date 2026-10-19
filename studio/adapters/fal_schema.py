"""Turn fal's own OpenAPI input schema into this platform's capability profile.

Why this module exists
----------------------
The tempting shortcut when integrating a model provider is to guess: it is an
image model, so it must support inpainting; it takes an image, so it must accept a
mask. Every such guess is a promise the platform cannot keep, and the failure
arrives after the user has paid for a request.

fal publishes a machine-readable input schema for each endpoint at

    https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<endpoint_id>

reachable without a credential. So there is no need to guess. This module reads
that schema and derives, from the provider's own declarations:

  * which studio modes the endpoint can actually serve, and which it cannot,
  * the exact parameter set, with the provider's own defaults, enum members and
    numeric bounds,
  * which input field carries an image, a mask, a first frame or a last frame,
  * whether the result is an image or a video.

The translation is deliberately conservative in one direction only: a field this
module does not recognise is not silently dropped, it is carried through as a
typed control (or, if untypable, as a noted restriction). And a mode is claimed
only when a field that could plausibly implement it is present and named -- see
the FIELD_ROLES table.

Nothing here interprets or executes anything. It reads JSON property names and
type annotations and produces data structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import ApiError, INVALID_RESPONSE
from ..util import truncate
from .base import (
    ParameterSchema,
    ParameterSpec,
    REFERENCE_ROLES,
)
from .types import CapabilityProfile

#: The route that returns a single endpoint's queue OpenAPI document. It is
#: anonymous: fal answers it without a key, which is why capability discovery
#: never has to send a user's credential anywhere it is not needed.
SCHEMA_URL = "https://fal.ai/api/openapi/queue/openapi.json"

#: How many parameters to surface on one model before moving the rest to advanced.
MAX_SURFACED_PARAMETERS = 64


def schema_url(endpoint_id: str) -> str:
    from urllib.parse import quote

    return SCHEMA_URL + "?endpoint_id=" + quote(endpoint_id, safe="")


# ---------------------------------------------------------------------------
# Which input field plays which role
# ---------------------------------------------------------------------------

#: Ordered longest-first matching so `image_urls` is not mistaken for `image_url`.
FIELD_ROLES: Tuple[Tuple[str, str], ...] = (
    ("mask_url", "mask"),
    ("mask_image", "mask"),
    ("first_frame_image", "first_frame"),
    ("start_image", "first_frame"),
    ("end_image_url", "last_frame"),
    ("last_frame_image", "last_frame"),
    ("tail_image_url", "last_frame"),
    ("input_reference", "first_frame"),
    ("reference_images", "reference"),
    ("reference_image_url", "reference"),
    ("image_urls", "reference"),
    ("images", "reference"),
    ("image_url", "image"),
    ("init_image", "image"),
    ("source_image", "image"),
    ("image", "image"),
    ("video_urls", "reference_video"),
    ("video_url", "video"),
    ("reference_video_url", "reference_video"),
    ("video", "video"),
    ("audio_url", "audio"),
    ("init_audio_url", "audio"),
    ("audio", "audio"),
)

#: The prompt field. Its name is stable across fal's catalog.
PROMPT_FIELDS = ("prompt", "text", "description")

#: Fields that never belong in a user-facing control: transport switches the
#: platform sets itself, and provider plumbing it must not expose.
INTERNAL_FIELDS = frozenset(
    {
        "sync_mode",
        "webhook_url",
        "logs",
        "idempotency_key",
        "store_output",
    }
)

#: Where a parameter belongs in the studio's control groups. The first prefix that
#: matches wins, so order matters.
GROUP_BY_FIELD: Tuple[Tuple[str, str], ...] = (
    ("camera", "camera"),
    ("aspect_ratio", "output"),
    ("resolution", "output"),
    ("image_size", "output"),
    ("size", "output"),
    ("duration", "output"),
    ("seconds", "output"),
    ("num_frames", "output"),
    ("frames", "output"),
    ("fps", "output"),
    ("num_images", "output"),
    ("num_videos", "output"),
    ("batch_size", "output"),
    ("output_format", "output"),
    ("output_quality", "output"),
    ("generate_audio", "output"),
    ("seed", "quality"),
    ("negative_prompt", "content"),
    ("strength", "quality"),
    ("num_inference_steps", "quality"),
    ("num_steps", "quality"),
    ("guidance_scale", "quality"),
    ("guidance", "quality"),
    ("acceleration", "quality"),
    ("video_quality", "quality"),
    ("video_write_mode", "quality"),
    ("use_turbo", "quality"),
    ("safety", "provider"),
    ("prompt_optimizer", "provider"),
    ("enable_prompt_expansion", "provider"),
    ("auto_fix", "provider"),
    ("enhance_prompt", "provider"),
    ("limit_generations", "provider"),
    ("enable_", "provider"),
    ("disable_", "provider"),
)

#: Parameter names the studio treats as advanced by default: they work, but they
#: are not the first thing a person reaching for a picture should have to read.
ADVANCED_FIELDS = frozenset(
    {
        "seed",
        "num_inference_steps",
        "num_steps",
        "guidance_scale",
        "guidance",
        "acceleration",
        "sync_mode",
        "enable_safety_checker",
        "safety_tolerance",
        "enable_prompt_expansion",
        "prompt_optimizer",
        "auto_fix",
        "enhance_prompt",
        "enhance_prompt_mode",
        "limit_generations",
        "use_turbo",
        "output_format",
        "output_quality",
        "video_write_mode",
        "limit_images",
        "max_images",
        "fps",
        "frames",
        "num_frames",
    }
)

#: Aspect-ratio-ish enum members this platform knows how to label.
KNOWN_ASPECT_RATIOS = (
    "21:9",
    "16:9",
    "3:2",
    "4:3",
    "5:4",
    "1:1",
    "4:5",
    "3:4",
    "2:3",
    "9:16",
    "auto",
)


#: Names that look like a media field but are counts, switches or seeds. Without
#: this list num_images reads as an image list and image_size reads as an input
#: picture, which is how a text-to-image model acquires capabilities it does not have.
NOT_A_MEDIA_FIELD = frozenset(
    {
        "num_images",
        "num_videos",
        "num_frames",
        "num_reference_images",
        "max_images",
        "image_size",
        "video_size",
        "image_format",
        "image_quality",
        "image_strength",
        "image_weight",
        "reference_strength",
        "seed",
    }
)
NON_MEDIA_PREFIXES = (
    "num_",
    "max_",
    "min_",
    "n_",
    "use_",
    "enable_",
    "disable_",
    "has_",
    "batch_",
    "count_",
    "generate_",
)
NON_MEDIA_SUFFIXES = ("_size", "_count", "_format", "_quality", "_scale", "_weight", "_strength", "_seed")


def role_for(field_name: str) -> Optional[str]:
    """Which role a provider input field plays, if any.

    Exact names win, then a suffix match, so end_image_url resolves to the
    last-frame role before the generic image role can claim it. Anything that
    looks like a count, a toggle or a dimension is never a media field.
    """
    name = field_name.lower()
    if name in NOT_A_MEDIA_FIELD:
        return None
    if name.startswith(NON_MEDIA_PREFIXES):
        return None
    if name.endswith(NON_MEDIA_SUFFIXES):
        return None
    for candidate, role in FIELD_ROLES:
        if name == candidate:
            return role
    for candidate, role in FIELD_ROLES:
        if name.endswith(candidate):
            return role
    return None


def group_for(field_name: str) -> str:
    name = field_name.lower()
    for prefix, group in GROUP_BY_FIELD:
        if name.startswith(prefix):
            return group
    return "settings"


def _spec_for_role(role: str, field_name: str) -> ParameterSpec:
    if role == "image":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="Input image",
            description="A URL to the image this endpoint will work from.",
            group="content",
            api_key=field_name,
        )
    if role == "reference":
        return ParameterSpec(
            name=field_name,
            type="array",
            item_type="string",
            label="Reference images",
            description="URLs to the images this endpoint will draw on.",
            group="content",
            api_key=field_name,
        )
    if role == "first_frame":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="First frame",
            description="A URL to the still the clip starts from.",
            group="content",
            api_key=field_name,
        )
    if role == "last_frame":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="Last frame",
            description="A URL to the still the clip should end on.",
            group="content",
            api_key=field_name,
        )
    if role == "mask":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="Mask",
            description="A URL to a black-and-white image marking the area to change.",
            group="content",
            api_key=field_name,
        )
    if role == "video":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="Input video",
            description="A URL to the video this endpoint will work from.",
            group="content",
            api_key=field_name,
        )
    if role == "reference_video":
        return ParameterSpec(
            name=field_name,
            type="array",
            item_type="string",
            label="Reference videos",
            description="URLs to videos this endpoint will draw on.",
            group="content",
            api_key=field_name,
        )
    if role == "audio":
        return ParameterSpec(
            name=field_name,
            type="string",
            label="Input audio",
            description="A URL to the audio this endpoint will work from.",
            group="content",
            api_key=field_name,
        )
    return ParameterSpec(name=field_name, type="string", label=field_name.replace("_", " ").title())


@dataclass
class InputField:
    """One property of the endpoint's input schema, normalised."""

    name: str
    json_types: Tuple[str, ...]
    title: str = ""
    description: str = ""
    default: Any = None
    enum: Tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    item_type: Optional[str] = None
    required: bool = False
    role: Optional[str] = None

    @property
    def label(self) -> str:
        if self.title:
            return self.title
        return self.name.replace("_", " ").replace("-", " ").strip().title()

    @property
    def is_nullable(self) -> bool:
        return "null" in self.json_types

    def primary_type(self) -> str:
        for candidate in self.json_types:
            if candidate != "null":
                return candidate
        return "string"


# ---------------------------------------------------------------------------
# JSON Schema reading
# ---------------------------------------------------------------------------


def _types_of(prop: Dict[str, Any]) -> Tuple[str, ...]:
    """Collect the JSON types a property allows, flattening `anyOf`/`oneOf`."""
    found: List[str] = []

    def absorb(node: Any) -> None:
        if not isinstance(node, dict):
            return
        declared = node.get("type")
        if isinstance(declared, str) and declared not in found:
            found.append(declared)
        elif isinstance(declared, list):
            for entry in declared:
                if isinstance(entry, str) and entry not in found:
                    found.append(entry)
        for key in ("anyOf", "oneOf", "allOf"):
            branch = node.get(key)
            if isinstance(branch, list):
                for child in branch:
                    absorb(child)
        # A `$ref` to a schema we cannot see still means "a value of some kind".
        if not found and "$ref" in node:
            found.append("object")

    absorb(prop)
    if not found:
        # `type` omitted but an `enum` given: infer from the members.
        members = prop.get("enum")
        if isinstance(members, list) and members:
            sample = next((m for m in members if m is not None), None)
            if isinstance(sample, bool):
                found.append("boolean")
            elif isinstance(sample, int):
                found.append("integer")
            elif isinstance(sample, float):
                found.append("number")
            elif isinstance(sample, str):
                found.append("string")
    return tuple(found) or ("string",)


def _enum_of(prop: Dict[str, Any]) -> Tuple[str, ...]:
    members = prop.get("enum")
    if not isinstance(members, list):
        for key in ("anyOf", "oneOf"):
            branch = prop.get(key)
            if isinstance(branch, list):
                for child in branch:
                    if isinstance(child, dict) and isinstance(child.get("enum"), list):
                        members = child["enum"]
                        break
            if members:
                break
    if not isinstance(members, list):
        return ()
    out: List[str] = []
    for member in members:
        if member is None or isinstance(member, (dict, list)):
            continue
        text = str(member)
        if text not in out:
            out.append(text)
    return tuple(out)


def _bound(prop: Dict[str, Any], key: str) -> Optional[float]:
    value = prop.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    for branch_key in ("anyOf", "oneOf"):
        branch = prop.get(branch_key)
        if isinstance(branch, list):
            for child in branch:
                if isinstance(child, dict):
                    inner = _bound(child, key)
                    if inner is not None:
                        return inner
    return None


def read_field(name: str, prop: Dict[str, Any], required: bool) -> InputField:
    types = _types_of(prop)
    item_type: Optional[str] = None
    items = prop.get("items")
    if isinstance(items, dict):
        item_types = _types_of(items)
        item_type = item_types[0] if item_types else None
    role = role_for(name)
    # A media role is a claim that a field carries an image, a mask or a clip. A
    # switch or a count can never be one, so the declared type gets the last word:
    # \`generate_audio\` is a boolean even though its name ends in "audio", and
    # \`image_size\` is a size choice even though its name starts with "image".
    if role is not None and not ({"string", "array"} & set(types)):
        role = None
    return InputField(
        name=name,
        json_types=types,
        title=str(prop.get("title") or ""),
        description=str(prop.get("description") or ""),
        default=prop.get("default"),
        enum=_enum_of(prop),
        minimum=_bound(prop, "minimum"),
        maximum=_bound(prop, "maximum"),
        item_type=item_type,
        required=required,
        role=role,
    )


# ---------------------------------------------------------------------------
# The compiled schema
# ---------------------------------------------------------------------------


@dataclass
class FalSchema:
    """One endpoint's declared interface, compiled into platform terms."""

    endpoint_id: str
    title: str = ""
    category: str = ""
    description: str = ""
    documentation_url: str = ""
    input_schema_name: str = ""
    output_schema_name: str = ""
    modality: str = "other"
    fields: Tuple[InputField, ...] = ()
    input_properties: Tuple[str, ...] = ()
    output_properties: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    #: True when the document parsed but declared nothing usable; the caller then
    #: falls back to a hand-curated table or a narrow hand-registered profile.
    is_empty: bool = False

    # -- discovery helpers -------------------------------------------------

    def field(self, name: str) -> Optional[InputField]:
        for entry in self.fields:
            if entry.name == name:
                return entry
        return None

    def fields_with_role(self, role: str) -> Tuple[InputField, ...]:
        return tuple(entry for entry in self.fields if entry.role == role)

    def start_frame_field(self) -> Optional[InputField]:
        """The field carrying the clip's opening still, whatever the provider calls it."""
        return self.first_field_with_role("first_frame") or self.first_field_with_role("image")

    def first_field_with_role(self, role: str) -> Optional[InputField]:
        found = self.fields_with_role(role)
        return found[0] if found else None

    def has_outpaint_controls(self) -> bool:
        for entry in self.fields:
            if entry.name.startswith("expand_") or entry.name == "zoom_out_percentage":
                return True
        return False

    # -- capability derivation --------------------------------------------

    def modes(self) -> Tuple[str, ...]:
        """The studio modes this endpoint can serve, derived from its fields.

        The rule throughout is that a mode is claimed only when a specific field
        that could implement it exists. A model that takes a prompt and nothing
        else is a text-to-image model even if its description boasts about
        editing, because there is no way to hand it the picture to edit.
        """
        modes: List[str] = []
        mask = self.first_field_with_role("mask")
        image = self.first_field_with_role("image")
        reference = self.first_field_with_role("reference")
        first_frame = self.first_field_with_role("first_frame")
        last_frame = self.first_field_with_role("last_frame")
        video = self.first_field_with_role("video")
        audio = self.first_field_with_role("audio")
        prompt_field = self.prompt_field()

        if self.modality == "video":
            # On a video endpoint an image input is the frame the clip starts from.
            # A provider that names it image_url rather than first_frame_image
            # means the same thing, and the ending frame is what turns one still into
            # an interpolation between two.
            start_frame = first_frame if first_frame is not None else image
            takes_picture = start_frame is not None or reference is not None
            if video is not None:
                modes.append("video_to_video")
                if video.json_types == ("array",) or video.name.endswith("s"):
                    modes.append("reference_video")
            if not takes_picture and video is None:
                modes.append("text_to_video")
            if start_frame is not None and last_frame is not None:
                modes.append("first_last_frame")
            if start_frame is not None:
                modes.append("image_to_video")
                modes.append("first_frame")
            if reference is not None and start_frame is None:
                modes.append("reference_video")
            if audio is not None and takes_picture:
                modes.append("lip_sync")
            if audio is not None and video is not None:
                modes.append("lip_sync")
            if "extend" in self.endpoint_id or "extend" in self.category:
                modes.append("extend")
            if "upscale" in self.endpoint_id or "upscale" in self.category:
                modes.append("upscale")
            if "restyle" in self.endpoint_id or "style" in self.category:
                modes.append("restyle")
            if "sequence" in self.endpoint_id or "scene" in self.category:
                modes.append("scene_continuation")
            if "storyboard" in self.endpoint_id or "multi" in self.endpoint_id.split("/")[-1]:
                modes.append("multi_shot")
        else:
            takes_picture = image is not None or reference is not None
            if not takes_picture:
                modes.append("text_to_image")
            else:
                modes.append("image_to_image")
                if reference is not None:
                    modes.append("reference_guided")
                    if reference.json_types == ("array",) or (reference.maximum or 0) > 1:
                        modes.append("multi_reference")
                if "style" in self.endpoint_id or "restyle" in self.endpoint_id:
                    modes.append("style_transfer")
            if mask is not None:
                modes.append("inpaint")
            if self.has_outpaint_controls() or "outpaint" in self.endpoint_id:
                modes.append("outpaint")
            if "upscale" in self.endpoint_id or "upscale" in self.category:
                modes.append("upscale")
            if "background/remove" in self.endpoint_id or "rembg" in self.endpoint_id:
                modes.append("background_remove")
            if "background" in self.endpoint_id and "replace" in self.endpoint_id:
                modes.append("background_replace")
            if "eraser" in self.endpoint_id or "object-removal" in self.endpoint_id:
                modes.append("object_remove")
            if "enhance" in self.endpoint_id or "relight" in self.endpoint_id:
                modes.append("enhance")
            if self.multiple_outputs_possible():
                modes.append("variations")

        # Preserve declaration order but drop duplicates and unknown vocabulary.
        from .base import IMAGE_MODES, VIDEO_MODES

        vocabulary = VIDEO_MODES if self.modality == "video" else IMAGE_MODES
        seen: List[str] = []
        for mode in modes:
            if mode and mode in vocabulary and mode not in seen:
                seen.append(mode)
        return tuple(seen)

    def multiple_outputs_possible(self) -> bool:
        for entry in self.fields:
            if entry.name in ("num_images", "num_videos", "num_outputs", "batch_size"):
                ceiling = entry.maximum if entry.maximum is not None else 1
                if ceiling <= 1:
                    return False
                return True
        return False

    def max_outputs(self) -> int:
        for entry in self.fields:
            if entry.name in ("num_images", "num_videos", "num_outputs", "batch_size"):
                if entry.maximum is not None:
                    return max(1, min(16, int(entry.maximum)))
                if isinstance(entry.default, int) and entry.default > 0:
                    return max(1, min(16, entry.default))
        return 1

    def prompt_field(self) -> Optional[InputField]:
        for name in PROMPT_FIELDS:
            found = self.field(name)
            if found is not None:
                return found
        return None

    def prompt_required(self) -> bool:
        found = self.prompt_field()
        return bool(found and found.required)

    def aspect_ratios(self) -> Tuple[str, ...]:
        for entry in self.fields:
            if entry.name in ("aspect_ratio", "aspect", "ratio") and entry.enum:
                return entry.enum
        return ()

    def resolutions(self) -> Tuple[str, ...]:
        for entry in self.fields:
            if entry.name in ("resolution", "video_resolution", "size") and entry.enum:
                return entry.enum
        return ()

    def durations_seconds(self) -> Tuple[float, ...]:
        for entry in self.fields:
            if entry.name in ("duration", "seconds", "duration_seconds") and entry.enum:
                values: List[float] = []
                for member in entry.enum:
                    try:
                        values.append(float(str(member).rstrip("s")))
                    except ValueError:
                        continue
                if values:
                    return tuple(sorted(values))
        return ()

    def frame_rates(self) -> Tuple[float, ...]:
        for entry in self.fields:
            if entry.name in ("fps", "frame_rate") and entry.enum:
                values = []
                for member in entry.enum:
                    try:
                        values.append(float(member))
                    except ValueError:
                        continue
                if values:
                    return tuple(sorted(values))
        return ()

    def reference_roles(self) -> Tuple[str, ...]:
        roles: List[str] = []
        for entry in self.fields_with_role("reference") + self.fields_with_role("image"):
            for candidate in ("main_subject", "composition", "style", "product", "environment", "color_palette"):
                if candidate not in roles:
                    roles.append(candidate)
        for entry in self.fields_with_role("first_frame"):
            if "first_frame" not in roles:
                roles.append("first_frame")
        for entry in self.fields_with_role("last_frame"):
            if "last_frame" not in roles:
                roles.append("last_frame")
        return tuple(role for role in roles if role in REFERENCE_ROLES)

    def restrictions(self) -> Tuple[str, ...]:
        """What this endpoint cannot do, stated as fact rather than omission."""
        notes: List[str] = []
        mask = self.first_field_with_role("mask")
        if self.modality == "image" and mask is None and (self.fields_with_role("image") or self.fields_with_role("reference")):
            notes.append(
                "The provider's schema for this endpoint has no mask input, so masked inpainting and canvas "
                "expansion are not available on it. It edits the whole frame from your text instruction."
            )
        if self.modality == "image" and not (self.fields_with_role("image") or self.fields_with_role("reference")):
            notes.append(
                "The provider's schema for this endpoint takes no image input, so editing, reference-guided "
                "generation and inpainting are unavailable here. Choose an editing endpoint to work from a picture."
            )
        if self.modality == "video":
            if self.first_field_with_role("last_frame") is None and self.start_frame_field() is not None:
                notes.append(
                    "The schema declares a starting frame but no ending frame, so this endpoint cannot interpolate "
                    "between two stills."
                )
            if self.start_frame_field() is None:
                notes.append(
                    "The schema takes no image input, so image-to-video, first-frame and last-frame control are "
                    "unavailable on this endpoint."
                )
        for warning in self.warnings:
            notes.append(warning)
        return tuple(notes)

    def capability_profile(self) -> CapabilityProfile:
        mask = self.first_field_with_role("mask")
        reference_field = self.first_field_with_role("reference")
        max_reference = 0
        if reference_field is not None:
            max_reference = int(reference_field.maximum) if reference_field.maximum else 10
        elif self.first_field_with_role("image") is not None:
            max_reference = 1
        notes: List[str] = []
        if self.description:
            notes.append(truncate(" ".join(self.description.split()), 400))
        notes.append(
            "Capabilities read from the provider's own OpenAPI schema for "
            + self.endpoint_id
            + ", not inferred from the model's category."
        )
        return CapabilityProfile(
            modality=self.modality,
            modes=self.modes(),
            aspect_ratios=self.aspect_ratios(),
            resolutions=self.resolutions(),
            durations_seconds=self.durations_seconds(),
            frame_rates=self.frame_rates(),
            reference_roles=self.reference_roles(),
            max_reference_images=max_reference,
            max_outputs=self.max_outputs(),
            supports_negative_prompt=any(e.name == "negative_prompt" for e in self.fields),
            supports_seed=any(e.name == "seed" for e in self.fields),
            supports_audio=any(e.name in ("generate_audio", "audio_url", "audio") for e in self.fields),
            supports_mask=mask is not None,
            native_camera_moves=self.native_camera_moves(),
            restrictions=self.restrictions(),
            notes=tuple(notes),
        )

    def native_camera_moves(self) -> Tuple[str, ...]:
        """Camera moves this endpoint accepts as a real parameter.

        Only a field whose own name says "camera" counts. A `motion` or
        `prompt` field is not evidence that the model obeys camera language,
        so no move is claimed from it.
        """
        moves: List[str] = []
        for entry in self.fields:
            if not entry.name.startswith("camera"):
                continue
            for member in entry.enum or ():
                if member not in moves:
                    moves.append(member)
        return tuple(moves)

    # -- parameter schema -------------------------------------------------

    def parameter_schema(self) -> ParameterSchema:
        specs: List[ParameterSpec] = []
        for entry in self.fields:
            if entry.name in INTERNAL_FIELDS:
                specs.append(_internal_spec(entry))
                continue
            if entry.role is not None:
                specs.append(_spec_for_role(entry.role, entry.name))
                continue
            if entry.name in PROMPT_FIELDS:
                continue
            spec = _control_spec(entry)
            if spec is not None:
                specs.append(spec)
        return ParameterSchema(specs=specs[:MAX_SURFACED_PARAMETERS])

    def to_public(self) -> Dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "title": self.title,
            "category": self.category,
            "modality": self.modality,
            "modes": list(self.modes()),
            "documentation_url": self.documentation_url,
            "input_properties": list(self.input_properties),
            "output_properties": list(self.output_properties),
            "restrictions": list(self.restrictions()),
            "source": schema_url(self.endpoint_id),
        }


def _internal_spec(entry: InputField) -> ParameterSpec:
    return ParameterSpec(
        name=entry.name,
        type="boolean" if entry.primary_type() == "boolean" else "string",
        label=entry.label,
        description=truncate(entry.description or "A transport option this platform manages.", 400),
        default=entry.default if isinstance(entry.default, bool) else None,
        group="provider",
        advanced=True,
    )


def _control_spec(entry: InputField) -> Optional[ParameterSpec]:
    """Build a control from a provider field, or skip it when it is unusable."""
    declared = entry.primary_type()
    label = entry.label
    description = truncate(" ".join(entry.description.split()), 400)
    group = group_for(entry.name)
    advanced = entry.name in ADVANCED_FIELDS

    if entry.enum:
        return ParameterSpec(
            name=entry.name,
            type="enum",
            label=label,
            description=description,
            default=entry.default if entry.default in entry.enum else entry.enum[0],
            options=entry.enum,
            group=group,
            advanced=advanced,
        )

    if declared == "boolean":
        return ParameterSpec(
            name=entry.name,
            type="boolean",
            label=label,
            description=description,
            default=bool(entry.default) if isinstance(entry.default, bool) else False,
            group=group,
            advanced=advanced,
        )
    if declared == "integer":
        return ParameterSpec(
            name=entry.name,
            type="integer",
            label=label,
            description=description,
            default=entry.default if isinstance(entry.default, int) and not isinstance(entry.default, bool) else None,
            minimum=entry.minimum,
            maximum=entry.maximum,
            group=group,
            advanced=advanced,
        )
    if declared == "number":
        return ParameterSpec(
            name=entry.name,
            type="number",
            label=label,
            description=description,
            default=entry.default if isinstance(entry.default, (int, float)) and not isinstance(entry.default, bool) else None,
            minimum=entry.minimum,
            maximum=entry.maximum,
            group=group,
            advanced=True,
        )
    if declared == "string":
        return ParameterSpec(
            name=entry.name,
            type="string",
            label=label,
            description=description,
            default=entry.default if isinstance(entry.default, str) else None,
            max_length=2000,
            group=group,
            advanced=advanced,
        )
    if declared == "array":
        return ParameterSpec(
            name=entry.name,
            type="array",
            label=label,
            description=description,
            item_type=entry.item_type or "string",
            group=group,
            advanced=True,
        )
    if declared == "object":
        return ParameterSpec(
            name=entry.name,
            type="object",
            label=label,
            description=(description + " Accepts a structured value.")
            if description
            else "Accepts a structured value.",
            group=group,
            advanced=True,
        )
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _pick_schema(schemas: Dict[str, Any], want: str) -> Tuple[str, Dict[str, Any]]:
    """Find the input or output schema in a fal OpenAPI document.

    fal names these after the endpoint (Veo3FastInput), so the name is guessed
    from the suffix rather than hard-coded.
    """
    for name, body in schemas.items():
        if name.endswith(want) and isinstance(body, dict) and "properties" in body:
            return name, body
    for name, body in schemas.items():
        lowered = name.lower()
        if want == "Input" and "input" in lowered and isinstance(body, dict) and "properties" in body:
            return name, body
        if want == "Output" and "output" in lowered and isinstance(body, dict) and "properties" in body:
            return name, body
    return "", {}


def _resolve(ref: str, schemas: Dict[str, Any]) -> Dict[str, Any]:
    """Follow a local `$ref` one level so a field's real type is visible."""
    marker = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(marker):
        return {}
    target = schemas.get(ref[len(marker):])
    return target if isinstance(target, dict) else {}


def _modality_from_output(output_props: Sequence[str], category: str) -> str:
    lowered = {name.lower() for name in output_props}
    if {"video", "video_url", "videos"} & lowered:
        return "video"
    if {"images", "image", "image_url"} & lowered:
        return "image"
    if {"audio", "audio_file", "audio_url"} & lowered:
        return "other"
    if category.startswith("text-to-video") or category.endswith("to-video"):
        return "video"
    if category.endswith("to-image") or "image" in category:
        return "image"
    return "other"


def compile_schema(document: Dict[str, Any], endpoint_id: str = "") -> FalSchema:
    """Compile a fal queue OpenAPI document into a FalSchema.

    A document that does not look like one raises rather than quietly producing an
    empty profile, because an empty profile would let the studio show a model with
    no capabilities and no explanation.
    """
    if not isinstance(document, dict):
        raise ApiError(INVALID_RESPONSE, "The endpoint did not return a schema document.")
    schemas = ((document.get("components") or {}).get("schemas")) or {}
    if not isinstance(schemas, dict) or not schemas:
        raise ApiError(
            INVALID_RESPONSE,
            "The provider's schema document carried no component schemas, so this endpoint cannot be profiled from it.",
        )

    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    metadata = info.get("x-fal-metadata") if isinstance(info.get("x-fal-metadata"), dict) else {}
    resolved_id = endpoint_id or str(metadata.get("endpointId") or "")

    input_name, input_body = _pick_schema(schemas, "Input")
    output_name, output_body = _pick_schema(schemas, "Output")
    if not input_body:
        raise ApiError(
            INVALID_RESPONSE,
            "The provider's schema document declares no input object for "
            + (resolved_id or "this endpoint")
            + ".",
        )

    required_names = set()
    raw_required = input_body.get("required")
    if isinstance(raw_required, list):
        required_names = {str(entry) for entry in raw_required}

    properties = input_body.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}

    fields: List[InputField] = []
    warnings: List[str] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            prop = {}
        expanded = dict(prop)
        if "$ref" in prop:
            target = _resolve(prop["$ref"], schemas)
            if target:
                merged = dict(target)
                merged.update({k: v for k, v in prop.items() if k != "$ref"})
                expanded = merged
        entry = read_field(str(name), expanded, str(name) in required_names)
        if entry.role is None and not entry.json_types:
            warnings.append(
                "The provider field '" + str(name) + "' declares no usable type, so it is not offered as a control."
            )
            continue
        fields.append(entry)

    output_properties: List[str] = []
    output_required = set()
    if output_body:
        output_properties = [str(k) for k in (output_body.get("properties") or {})]
        if isinstance(output_body.get("required"), list):
            output_required = {str(k) for k in output_body["required"]}

    category = str(metadata.get("category") or "")
    modality = _modality_from_output(output_properties, category)

    compiled = FalSchema(
        endpoint_id=resolved_id,
        title=str(metadata.get("display_name") or info.get("title") or resolved_id),
        category=category,
        description=str(metadata.get("about") or info.get("description") or ""),
        documentation_url=str(metadata.get("documentationUrl") or ""),
        input_schema_name=input_name,
        output_schema_name=output_name,
        modality=modality,
        fields=tuple(fields),
        input_properties=tuple(str(k) for k in properties),
        output_properties=tuple(output_properties),
        warnings=tuple(warnings),
    )
    compiled.is_empty = not fields
    if compiled.is_empty:
        compiled.warnings = tuple(warnings) + (
            "The provider's schema declares no input fields, so no modes or parameters can be derived from it.",
        )
    return compiled


def compile_curated(facts: Any) -> FalSchema:
    """Wrap a hand-curated endpoint table entry as a FalSchema.

    Used when the schema route is unreachable. The resulting profile is the one
    transcribed from this repository's copy of fal's documents, and its notes say
    so, so a reader can tell a live read from a stored copy.
    """
    fields: List[InputField] = []
    if getattr(facts, "image_field", None):
        is_list = bool(getattr(facts, "image_field_is_list", False))
        fields.append(
            InputField(
                name=facts.image_field,
                json_types=("array",) if is_list else ("string",),
                title="Input image",
                required=True,
                role="reference" if is_list else "image",
                item_type="string" if is_list else None,
                maximum=float(getattr(facts, "max_reference_images", 0) or 0) or None,
            )
        )
    if getattr(facts, "last_frame_field", None):
        fields.append(
            InputField(
                name=facts.last_frame_field,
                json_types=("string", "null"),
                title="Last frame",
                role="last_frame",
            )
        )
    if getattr(facts, "mask_field", None):
        fields.append(
            InputField(
                name=facts.mask_field,
                json_types=("string",),
                title="Mask",
                required=True,
                role="mask",
            )
        )
    for spec in getattr(facts, "parameters", ()) or ():
        fields.append(
            InputField(
                name=spec.name,
                json_types=(_json_type_for(spec.type),),
                title=spec.label,
                description=spec.description,
                default=spec.default,
                enum=tuple(spec.options or ()) if spec.type == "enum" else (),
                minimum=spec.minimum,
                maximum=spec.maximum,
                item_type=spec.item_type,
                required=bool(spec.required),
                role=role_for(spec.name),
            )
        )
    schema = FalSchema(
        endpoint_id=getattr(facts, "key", ""),
        title=getattr(facts, "display_name", ""),
        category="",
        description=" ".join(getattr(facts, "notes", ()) or ()),
        modality=getattr(facts, "modality", "other"),
        fields=tuple(fields),
        input_properties=tuple(entry.name for entry in fields),
        output_properties=("video",) if getattr(facts, "modality", "") == "video" else ("images",),
        warnings=(
            "The provider's live schema could not be read, so this profile comes from the copy of fal's "
            "documentation stored with this deployment (" + str(getattr(facts, "source", "") or "unknown source") + ").",
        ),
    )
    return schema


def _json_type_for(spec_type: str) -> str:
    return {
        "string": "string",
        "number": "number",
        "integer": "integer",
        "boolean": "boolean",
        "enum": "string",
        "array": "array",
        "object": "object",
    }.get(spec_type, "string")


def summarize(schema: FalSchema) -> str:
    """A one-line human summary, for logs and the model inspector."""
    return (
        schema.endpoint_id
        + " ["
        + schema.modality
        + "] modes="
        + ",".join(schema.modes())
        + " params="
        + str(len(schema.parameter_schema()))
        + (" (from stored copy)" if schema.warnings else " (read live)")
    )