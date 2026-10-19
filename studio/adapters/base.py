"""The model adapter interface and the capability vocabulary built on top of it.

Everything in this module exists to keep one promise: the platform describes what
a model can actually do, and refuses to pretend otherwise. A model is not
assumed to support inpainting because it is an image model, and a camera move is
not advertised as a parameter when the provider only reads it as prose.

The vocabulary below is deliberately concrete. Modes name operations the studios
expose as buttons; a mode a model lacks is a button that is disabled with a
stated reason, never a button that fails after the user has written a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..errors import ApiError, CAPABILITY_UNSUPPORTED, VALIDATION_FAILED, validation_error


# ---------------------------------------------------------------------------
# Modality and generation modes
# ---------------------------------------------------------------------------


class Modality(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


#: Operations an image model may support. Each is a real, distinct request
#: shape: inpainting sends a mask, outpainting sends a canvas, upscaling sends
#: an image and no prompt, and so on.
IMAGE_MODES = (
    "text_to_image",
    "image_to_image",
    "inpaint",
    "outpaint",
    "variations",
    "background_replace",
    "background_remove",
    "object_add",
    "object_remove",
    "enhance",
    "upscale",
    "style_transfer",
    "reference_guided",
    "multi_reference",
    "batch",
)

#: Operations a video model may support.
VIDEO_MODES = (
    "text_to_video",
    "image_to_video",
    "first_frame",
    "first_last_frame",
    "video_to_video",
    "extend",
    "motion_transfer",
    "reference_video",
    "multi_shot",
    "scene_continuation",
    "restyle",
    "upscale",
    "audio",
    "lip_sync",
)

MODES_BY_MODALITY: Dict[str, Tuple[str, ...]] = {
    Modality.IMAGE.value: IMAGE_MODES,
    Modality.VIDEO.value: VIDEO_MODES,
    Modality.OTHER.value: (),
}

MODE_LABELS: Dict[str, str] = {
    "text_to_image": "Text to image",
    "image_to_image": "Image to image",
    "inpaint": "Inpaint (masked edit)",
    "outpaint": "Outpaint (canvas expand)",
    "variations": "Variations",
    "background_replace": "Background replace",
    "background_remove": "Background remove",
    "object_add": "Add object",
    "object_remove": "Remove object",
    "enhance": "Enhance",
    "upscale": "Upscale",
    "style_transfer": "Style transfer",
    "reference_guided": "Reference-guided",
    "multi_reference": "Multi-image reference",
    "batch": "Batch generation",
    "text_to_video": "Text to video",
    "image_to_video": "Image to video",
    "first_frame": "First frame to video",
    "first_last_frame": "First and last frame",
    "video_to_video": "Video transform",
    "extend": "Extend clip",
    "motion_transfer": "Motion transfer",
    "reference_video": "Reference-video guided",
    "multi_shot": "Multi-shot",
    "scene_continuation": "Scene continuation",
    "restyle": "Restyle",
    "upscale": "Upscale",
    "audio": "Audio generation",
    "lip_sync": "Lip sync",
}

#: Modes that require the caller to supply at least one input image. Used to give
#: a specific message ("this mode needs a source image") instead of a generic one.
MODES_REQUIRING_IMAGE = frozenset(
    {
        "image_to_image",
        "inpaint",
        "outpaint",
        "variations",
        "background_replace",
        "background_remove",
        "object_add",
        "object_remove",
        "enhance",
        "upscale",
        "style_transfer",
        "reference_guided",
        "multi_reference",
    }
)

#: Modes that require a mask. The distinction between a masked edit and a plain
#: image-to-image request is the whole reason inpaint is a separate mode.
MODES_REQUIRING_MASK = frozenset({"inpaint"})

#: Modes that require an input video.
MODES_REQUIRING_VIDEO = frozenset(
    {"video_to_video", "extend", "motion_transfer", "reference_video", "restyle", "lip_sync"}
)


def modes_for(modality: str) -> Tuple[str, ...]:
    return MODES_BY_MODALITY.get(str(modality), ())


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Reference images
# ---------------------------------------------------------------------------


#: A reference image is not one thing. A model that accepts a subject photo will
#: not necessarily accept a pose skeleton, so the role is part of the request and
#: part of the model's declared capability.
REFERENCE_ROLES = (
    "main_subject",
    "character_identity",
    "composition",
    "pose",
    "lighting",
    "color_palette",
    "environment",
    "product",
    "style",
    "first_frame",
    "last_frame",
)

REFERENCE_ROLE_LABELS: Dict[str, str] = {
    "main_subject": "Main subject",
    "character_identity": "Character identity",
    "composition": "Composition",
    "pose": "Pose",
    "lighting": "Lighting",
    "color_palette": "Color palette",
    "environment": "Environment",
    "product": "Product",
    "style": "Style",
    "first_frame": "First frame",
    "last_frame": "Last frame",
}


# ---------------------------------------------------------------------------
# Camera moves
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraMove:
    """A named camera instruction and how the selected model can honour it.

    The distinction this type makes is the honest one: some providers expose a
    camera field the model was trained to obey, and some read camera language in
    the prompt and nothing more. Both are useful. Presenting the second as if it
    were the first is not, because the user cannot then tell a control that is
    merely suggestive from one the model actually follows.
    """

    key: str
    label: str
    #: Prompt phrase used when the provider has no dedicated parameter.
    phrase: str
    #: Provider field, when one exists. None means the phrase goes in the prompt.
    parameter: Optional[str] = None
    #: Value to send that parameter, when it is an enum the provider expects.
    value: Optional[str] = None
    group: str = "movement"

    @property
    def is_native(self) -> bool:
        return self.parameter is not None


CAMERA_MOVES: Tuple[CameraMove, ...] = (
    CameraMove("static", "Static / locked off", "locked-off static camera, no movement"),
    CameraMove("dolly_in", "Dolly in", "slow dolly in", parameter="camera_move", value="dolly_in"),
    CameraMove("dolly_out", "Dolly out", "slow dolly out", parameter="camera_move", value="dolly_out"),
    CameraMove("push_in", "Push in", "push in toward the subject"),
    CameraMove("pull_out", "Pull out", "pull out away from the subject"),
    CameraMove("tracking", "Tracking shot", "tracking shot following the subject"),
    CameraMove("orbit", "Orbit", "orbiting camera around the subject"),
    CameraMove("pan", "Pan", "smooth horizontal pan"),
    CameraMove("tilt", "Tilt", "smooth vertical tilt"),
    CameraMove("crane_up", "Crane up", "crane up"),
    CameraMove("crane_down", "Crane down", "crane down"),
    CameraMove("handheld", "Handheld", "handheld camera, subtle natural shake"),
    CameraMove("whip_pan", "Whip pan", "fast whip pan"),
    CameraMove("fpv", "FPV drone", "FPV drone flight"),
    CameraMove("aerial", "Aerial", "aerial shot"),
    CameraMove("slow_motion", "Slow motion", "slow motion"),
    CameraMove("rack_focus", "Rack focus", "rack focus between foreground and background"),
    CameraMove("establishing", "Establishing shot", "wide establishing shot", group="framing"),
    CameraMove("close_up", "Close-up", "close-up shot", group="framing"),
    CameraMove("wide", "Wide shot", "wide shot", group="framing"),
    CameraMove(
        "over_the_shoulder",
        "Over the shoulder",
        "over-the-shoulder shot",
        group="framing",
    ),
)

CAMERA_MOVES_BY_KEY: Dict[str, CameraMove] = {move.key: move for move in CAMERA_MOVES}


def camera_move(key: str) -> Optional[CameraMove]:
    return CAMERA_MOVES_BY_KEY.get(key)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


#: The only value types a user-supplied parameter schema may declare. Anything
#: else -- a function name, a shell fragment, a reference to another field -- is
#: rejected at write time. This list is intentionally closed: a schema is data,
#: and data never becomes code.
PARAMETER_TYPES = ("string", "number", "integer", "boolean", "enum", "array", "object")

MAX_PARAMETERS_PER_MODEL = 64
MAX_ENUM_OPTIONS = 200
MAX_ARRAY_ITEMS = 64


@dataclass(frozen=True)
class ParameterSpec:
    """One declared control for a model, and the rules that govern its value."""

    name: str
    type: str
    label: str = ""
    description: str = ""
    default: Any = None
    required: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    options: Tuple[Any, ...] = ()
    option_labels: Mapping[str, str] = field(default_factory=dict)
    item_type: Optional[str] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    #: True when this control does not travel as an API field but is folded into
    #: the prompt text. The studio marks these so nobody mistakes a suggestion
    #: for a hard control.
    prompt_mapped: bool = False
    #: Provider field name when it differs from the schema name.
    api_key: Optional[str] = None
    group: str = "settings"
    advanced: bool = False

    @property
    def key(self) -> str:
        return self.api_key or self.name

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()

    def to_public(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "label": self.display_label,
            "description": self.description,
            "default": self.default,
            "required": self.required,
            "group": self.group,
            "advanced": self.advanced,
            "prompt_mapped": self.prompt_mapped,
        }
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.step is not None:
            out["step"] = self.step
        if self.options:
            out["options"] = [
                {"value": option, "label": self.option_labels.get(str(option), str(option))}
                for option in self.options
            ]
        if self.item_type:
            out["item_type"] = self.item_type
        if self.min_items is not None:
            out["min_items"] = self.min_items
        if self.max_items is not None:
            out["max_items"] = self.max_items
        if self.max_length is not None:
            out["max_length"] = self.max_length
        return out


def _type_problem(name: str, expected: str) -> ApiError:
    return validation_error(
        "The value for '%s' must be %s." % (name, expected), field=name
    )


class ParameterSchema:
    """An ordered, validated set of parameters for one model.

    Validation is strict on purpose. Sending a field a provider does not
    understand is at best ignored and at worst billed as a different request, so
    an unknown name is an error the user sees before anything is submitted, not
    something quietly dropped on the way out.
    """

    __slots__ = ("specs", "_by_name")

    def __init__(self, specs: Iterable[ParameterSpec] = ()) -> None:
        collected: List[ParameterSpec] = []
        seen = set()
        for spec in specs:
            if spec.name in seen:
                raise ApiError(
                    VALIDATION_FAILED,
                    "Duplicate parameter '%s' in schema." % spec.name,
                    field=spec.name,
                )
            seen.add(spec.name)
            collected.append(spec)
        self.specs: Tuple[ParameterSpec, ...] = tuple(collected)
        self._by_name: Dict[str, ParameterSpec] = {s.name: s for s in self.specs}

    def __len__(self) -> int:
        return len(self.specs)

    def __iter__(self):
        return iter(self.specs)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def get(self, name: str) -> Optional[ParameterSpec]:
        return self._by_name.get(name)

    def names(self) -> Tuple[str, ...]:
        return tuple(spec.name for spec in self.specs)

    def defaults(self) -> Dict[str, Any]:
        return {
            spec.name: spec.default
            for spec in self.specs
            if spec.default is not None and not spec.prompt_mapped
        }

    def to_public(self) -> Dict[str, Any]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for spec in self.specs:
            groups.setdefault(spec.group, []).append(spec.to_public())
        return {
            "parameters": [spec.to_public() for spec in self.specs],
            "groups": [
                {"name": name, "parameters": items}
                for name, items in groups.items()
            ],
        }

    # -- validation ---------------------------------------------------------

    def _coerce(self, spec: ParameterSpec, value: Any) -> Any:
        """Return the value as the provider should receive it, or raise."""
        if value is None:
            return None
        kind = spec.type
        if kind == "string":
            if not isinstance(value, str):
                raise _type_problem(spec.name, "text")
            if spec.max_length is not None and len(value) > spec.max_length:
                raise validation_error(
                    "'%s' is longer than the %d characters allowed."
                    % (spec.display_label, spec.max_length),
                    field=spec.name,
                )
            if spec.pattern and not re.match(spec.pattern, value):
                raise validation_error(
                    "'%s' does not match the required format." % spec.display_label,
                    field=spec.name,
                )
            return value
        if kind == "integer":
            if isinstance(value, bool):
                raise _type_problem(spec.name, "a whole number")
            if isinstance(value, float) and not value.is_integer():
                raise _type_problem(spec.name, "a whole number")
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise _type_problem(spec.name, "a whole number") from None
            return self._bound(spec, number)
        if kind == "number":
            if isinstance(value, bool):
                raise _type_problem(spec.name, "a number")
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise _type_problem(spec.name, "a number") from None
            return self._bound(spec, number)
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            raise _type_problem(spec.name, "true or false")
        if kind == "enum":
            candidate = value
            if candidate not in spec.options:
                # Accept the string form of a numeric option, because JSON form
                # fields deliver everything as text.
                for option in spec.options:
                    if str(option) == str(candidate):
                        return option
                raise validation_error(
                    "'%s' must be one of: %s."
                    % (
                        spec.display_label,
                        ", ".join(str(option) for option in spec.options),
                    ),
                    field=spec.name,
                    details={"allowed": list(spec.options)},
                )
            return candidate
        if kind == "array":
            if not isinstance(value, (list, tuple)):
                raise _type_problem(spec.name, "a list")
            items = list(value)
            if spec.min_items is not None and len(items) < spec.min_items:
                raise validation_error(
                    "'%s' needs at least %d entries." % (spec.display_label, spec.min_items),
                    field=spec.name,
                )
            if spec.max_items is not None and len(items) > spec.max_items:
                raise validation_error(
                    "'%s' accepts at most %d entries." % (spec.display_label, spec.max_items),
                    field=spec.name,
                )
            if len(items) > MAX_ARRAY_ITEMS:
                raise validation_error(
                    "'%s' accepts at most %d entries." % (spec.display_label, MAX_ARRAY_ITEMS),
                    field=spec.name,
                )
            item_type = spec.item_type or "string"
            return [self._coerce(replace(spec, type=item_type), item) for item in items]
        if kind == "object":
            if not isinstance(value, Mapping):
                raise _type_problem(spec.name, "an object")
            if len(value) > MAX_ARRAY_ITEMS:
                raise validation_error(
                    "'%s' has too many keys." % spec.display_label, field=spec.name
                )
            return {str(k): v for k, v in value.items()}
        raise validation_error(
            "'%s' declares an unsupported type." % spec.display_label, field=spec.name
        )

    @staticmethod
    def _bound(spec: ParameterSpec, number: Any) -> Any:
        if spec.minimum is not None and number < spec.minimum:
            raise validation_error(
                "'%s' must be at least %s." % (spec.display_label, spec.minimum),
                field=spec.name,
            )
        if spec.maximum is not None and number > spec.maximum:
            raise validation_error(
                "'%s' must be at most %s." % (spec.display_label, spec.maximum),
                field=spec.name,
            )
        return number

    def validate(
        self, values: Optional[Mapping[str, Any]] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Sort submitted values into API fields and prompt-mapped instructions.

        Returns a pair of dictionaries. The first is only ever populated with
        names the schema knows and the model's adapter is willing to send. The
        second holds the values that this model expresses in the prompt rather
        than as a field, so the caller can compose them into the text.

        Raises ApiError on anything invalid, including unknown names.
        """
        submitted = dict(values or {})
        unknown = [name for name in submitted if name not in self._by_name]
        if unknown:
            raise validation_error(
                "This model does not accept %s."
                % ", ".join("'%s'" % name for name in sorted(unknown)),
                details={"unsupported": sorted(unknown), "supported": list(self.names())},
            )
        payload: Dict[str, Any] = {}
        prompt_values: Dict[str, Any] = {}
        for name, raw in submitted.items():
            spec = self._by_name[name]
            if raw is None or raw == "":
                continue
            coerced = self._coerce(spec, raw)
            if coerced is None:
                continue
            if spec.prompt_mapped:
                prompt_values[name] = coerced
            else:
                payload[spec.key] = coerced
        missing = [
            spec.name
            for spec in self.specs
            if spec.required and not spec.prompt_mapped and spec.name not in payload
        ]
        if missing:
            raise validation_error(
                "%s is required."
                % ", ".join(self._by_name[name].display_label for name in missing),
                field=missing[0],
            )
        return payload, prompt_values


def describe_parameter_types() -> Dict[str, Any]:
    """Documentation payload for the schema builder in the connection wizard."""
    return {
        "types": [
            {
                "name": "string",
                "label": "Text",
                "fields": ["max_length", "pattern", "default"],
            },
            {
                "name": "number",
                "label": "Number",
                "fields": ["minimum", "maximum", "step", "default"],
            },
            {
                "name": "integer",
                "label": "Whole number",
                "fields": ["minimum", "maximum", "default"],
            },
            {"name": "boolean", "label": "On / off", "fields": ["default"]},
            {
                "name": "enum",
                "label": "Choice",
                "fields": ["options", "default"],
            },
            {
                "name": "array",
                "label": "List",
                "fields": ["item_type", "min_items", "max_items"],
            },
            {"name": "object", "label": "Structured value", "fields": []},
        ],
        "limits": {
            "max_parameters": MAX_PARAMETERS_PER_MODEL,
            "max_enum_options": MAX_ENUM_OPTIONS,
            "max_array_items": MAX_ARRAY_ITEMS,
        },
        "note": (
            "Parameter schemas describe a request body. They are validated as data and "
            "are never executed as code."
        ),
    }


def parse_parameter_schema(raw: Any) -> ParameterSchema:
    """Build a schema from untrusted stored JSON, rejecting anything malformed.

    This runs on values that arrived through an HTTP request, so it assumes
    nothing: every field is type-checked, the type list is closed, and a schema
    that would not render into a usable control is refused outright.
    """
    if raw in (None, "", {}):
        return ParameterSchema()
    if isinstance(raw, Mapping) and "parameters" in raw:
        raw = raw["parameters"]
    if not isinstance(raw, (list, tuple)):
        raise validation_error("A parameter schema must be a list of parameters.")
    if len(raw) > MAX_PARAMETERS_PER_MODEL:
        raise validation_error(
            "A model may declare at most %d parameters." % MAX_PARAMETERS_PER_MODEL
        )
    specs: List[ParameterSpec] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise validation_error("Each parameter must be an object.")
        name = entry.get("name")
        if not isinstance(name, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", name):
            raise validation_error(
                "Parameter names must start with a letter and use letters, digits, or underscores.",
                details={"name": name},
            )
        kind = entry.get("type")
        if kind not in PARAMETER_TYPES:
            raise validation_error(
                "Parameter '%s' has an unsupported type." % name,
                field=name,
                details={"allowed_types": list(PARAMETER_TYPES)},
            )
        # Stored schemas come back from to_public(), where an option is the
        # {"value", "label"} object the renderer needs. Accepting that shape here
        # is what makes a schema survive a save and a reload; a bare scalar is
        # still the form a hand-written schema uses.
        raw_options = tuple(entry.get("options") or ())
        option_labels: List[Any] = []
        options: List[Any] = []  # type: ignore[var-annotated]
        for option in raw_options:
            if isinstance(option, Mapping):
                value = option.get("value")
                options.append(value)
                option_labels.append(option.get("label", value))
            else:
                options.append(option)
                option_labels.append(option)
        if kind == "enum":
            if not options:
                raise validation_error(
                    "Parameter '%s' is a choice but lists no options." % name, field=name
                )
            if len(options) > MAX_ENUM_OPTIONS:
                raise validation_error(
                    "Parameter '%s' lists more than %d options." % (name, MAX_ENUM_OPTIONS),
                    field=name,
                )
            for option in options:
                if not isinstance(option, (str, int, float, bool)):
                    raise validation_error(
                        "Options for '%s' must be text, numbers, or true/false." % name,
                        field=name,
                    )
        labels = entry.get("option_labels")
        # A schema read back from to_public() carries its labels inside the option
        # objects; keep them so a round trip does not flatten "Vivid" to "vivid".
        merged_labels: Dict[str, Any] = dict(labels) if isinstance(labels, Mapping) else {}
        for option, label in zip(options, option_labels):
            if isinstance(option, (str, int, float, bool)) and label != option:
                merged_labels[str(option)] = label
        specs.append(
            ParameterSpec(
                name=name,
                type=str(kind),
                label=str(entry.get("label") or "").strip(),
                description=str(entry.get("description") or "").strip()[:500],
                default=entry.get("default"),
                required=bool(entry.get("required")),
                minimum=_number_or_none(entry.get("minimum")),
                maximum=_number_or_none(entry.get("maximum")),
                step=_number_or_none(entry.get("step")),
                options=options,
                option_labels=merged_labels,
                item_type=entry.get("item_type") if entry.get("item_type") in PARAMETER_TYPES else None,
                min_items=_int_or_none(entry.get("min_items")),
                max_items=_int_or_none(entry.get("max_items")),
                max_length=_int_or_none(entry.get("max_length")),
                pattern=_safe_pattern(entry.get("pattern"), name),
                prompt_mapped=bool(entry.get("prompt_mapped")),
                api_key=(str(entry["api_key"]) if entry.get("api_key") else None),
                group=str(entry.get("group") or "settings")[:40],
                advanced=bool(entry.get("advanced")),
            )
        )
    return ParameterSchema(specs)


def _number_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_pattern(value: Any, name: str) -> Optional[str]:
    """Compile a user pattern once, here, so no request path can hang on it."""
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 200:
        raise validation_error(
            "The pattern for '%s' is not a usable regular expression." % name, field=name
        )
    try:
        re.compile(value)
    except re.error:
        raise validation_error(
            "The pattern for '%s' is not a usable regular expression." % name, field=name
        ) from None
    return value
