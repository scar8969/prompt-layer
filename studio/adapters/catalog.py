"""The fal.ai parameter controls this platform exposes, with their real bounds.

Each spec here came from a document in this repository, not from memory: the
image specs from the OpenAPI files in promptlayer/vendor/fal/, the video specs
from the parameter tables in promptlayer/layers.json. Defaults, enum members and
min/max values are copied verbatim, so the studio never offers a control whose
value the provider would reject.

Where a spec states a default, that default is what the provider applies when the
field is absent. The studio therefore shows it but does not force it: an untouched
control sends nothing, which keeps the request exactly as small as the user
intended and avoids overriding a provider default that changes later.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

from .base import ParameterSpec

#: Where these tables came from, quoted in the model inspector in the UI.
SCHEMA_SOURCE = "promptlayer/vendor/fal/ and promptlayer/layers.json"

#: Named image frames fal documents for the ImageSize field.
IMAGE_SIZE_PRESETS: Tuple[str, ...] = (
    "square_hd",
    "square",
    "portrait_4_3",
    "portrait_16_9",
    "landscape_4_3",
    "landscape_16_9",
)

#: The same presets with the pixel dimensions fal's schema documents, used for
#: the "expected output" line under the size control.
FRAME_PIXELS: Dict[str, Tuple[int, int]] = {
    "square_hd": (1024, 1024),
    "square": (512, 512),
    "portrait_4_3": (768, 1024),
    "portrait_16_9": (720, 1280),
    "landscape_4_3": (1024, 768),
    "landscape_16_9": (1280, 720),
}

ASPECT_RATIOS: Tuple[str, ...] = (
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
)

VIDEO_ASPECT_RATIOS: Tuple[str, ...] = ("16:9", "9:16", "1:1")

SEEDREAM_SIZES: Tuple[str, ...] = IMAGE_SIZE_PRESETS + ("auto", "auto_2K", "auto_4K")


def _output_format(default: str, options: Tuple[str, ...]) -> ParameterSpec:
    return ParameterSpec(
        name="output_format",
        type="enum",
        label="File format",
        default=default,
        options=options,
        group="output",
        advanced=True,
    )


def _seed() -> ParameterSpec:
    return ParameterSpec(
        name="seed",
        type="integer",
        label="Seed",
        description="Fixes the random seed, so the same prompt and seed reproduce the same image.",
        group="output",
        advanced=True,
    )


def _safety_checker(default: bool) -> ParameterSpec:
    return ParameterSpec(
        name="enable_safety_checker",
        type="boolean",
        label="Provider safety checker",
        description="Turns the provider's own content filter on or off. It runs on the provider's side, not ours.",
        default=default,
        group="provider",
        advanced=True,
    )


def _safety_tolerance(default: str, options: Tuple[str, ...], note: str) -> ParameterSpec:
    return ParameterSpec(
        name="safety_tolerance",
        type="enum",
        label="Safety tolerance",
        description=note,
        default=default,
        options=options,
        group="provider",
        advanced=True,
    )


def _negative_prompt(default: Optional[str] = None) -> ParameterSpec:
    return ParameterSpec(
        name="negative_prompt",
        type="string",
        label="Negative prompt",
        description="What the model should avoid. Only endpoints whose schema has this field offer it.",
        default=default,
        max_length=2000,
        group="content",
    )


def _sync_mode() -> ParameterSpec:
    return ParameterSpec(
        name="sync_mode",
        type="boolean",
        label="Return media inline",
        description="The provider would embed the file in the response instead of returning a URL. This platform archives generated media itself, so this stays off.",
        default=False,
        group="provider",
        advanced=True,
    )


# ---------------------------------------------------------------------------
# Image parameters
# ---------------------------------------------------------------------------

IMAGE_PARAMETERS: Dict[str, ParameterSpec] = {
    "num_images": ParameterSpec(
        name="num_images",
        type="integer",
        label="Number of images",
        description="How many separate images this prompt produces. Each one is a separate generation the provider may bill for.",
        default=1,
        minimum=1,
        maximum=4,
        group="output",
    ),
    "image_size": ParameterSpec(
        name="image_size",
        type="enum",
        label="Size",
        description="A named frame from the endpoint's schema, or explicit pixel dimensions.",
        default="landscape_4_3",
        options=IMAGE_SIZE_PRESETS,
        group="output",
    ),
    "output_format": _output_format("png", ("jpeg", "png", "webp")),
    "seed": _seed(),
    "num_inference_steps": ParameterSpec(
        name="num_inference_steps",
        type="integer",
        label="Steps",
        description="More steps take longer and, on some endpoints, cost more.",
        default=28,
        minimum=4,
        maximum=50,
        group="quality",
        advanced=True,
    ),
    "guidance_scale": ParameterSpec(
        name="guidance_scale",
        type="number",
        label="Guidance",
        description="How strictly the model follows the prompt.",
        default=2.5,
        minimum=0.0,
        maximum=20.0,
        step=0.1,
        group="quality",
        advanced=True,
    ),
    "acceleration": ParameterSpec(
        name="acceleration",
        type="enum",
        label="Acceleration",
        description="A speed and quality tradeoff implemented by the provider.",
        default="regular",
        options=("none", "regular", "high"),
        group="quality",
        advanced=True,
    ),
    "enable_safety_checker": _safety_checker(True),
    "safety_tolerance": _safety_tolerance(
        "4",
        ("1", "2", "3", "4", "5", "6"),
        "1 is the strictest setting, 6 the most permissive.",
    ),
    "negative_prompt": _negative_prompt(),
    "enable_prompt_expansion": ParameterSpec(
        name="enable_prompt_expansion",
        type="boolean",
        label="Let the provider expand the prompt",
        description="The provider rewrites your prompt before generating. It changes the result, so it stays off unless you ask for it.",
        default=False,
        group="provider",
        advanced=True,
    ),
    "enhance_prompt_mode": ParameterSpec(
        name="enhance_prompt_mode",
        type="enum",
        label="Prompt enhancement",
        default="standard",
        options=("standard", "fast"),
        group="provider",
        advanced=True,
    ),
    "aspect_ratio": ParameterSpec(
        name="aspect_ratio",
        type="enum",
        label="Aspect ratio",
        default="1:1",
        options=ASPECT_RATIOS,
        group="output",
    ),
    "limit_generations": ParameterSpec(
        name="limit_generations",
        type="boolean",
        label="Limit generations",
        description="A provider-side restriction on how many images come back for one request.",
        default=True,
        group="provider",
        advanced=True,
    ),
    "use_turbo": ParameterSpec(
        name="use_turbo",
        type="boolean",
        label="Turbo",
        description="The provider's faster, lower-fidelity mode.",
        default=False,
        group="quality",
        advanced=True,
    ),
    "sync_mode": _sync_mode(),
    "max_images": ParameterSpec(
        name="max_images",
        type="integer",
        label="Maximum images",
        default=1,
        minimum=1,
        maximum=6,
        group="output",
        advanced=True,
    ),
}


# ---------------------------------------------------------------------------
# Video parameters
# ---------------------------------------------------------------------------

VIDEO_PARAMETERS: Dict[str, ParameterSpec] = {
    "aspect_ratio": ParameterSpec(
        name="aspect_ratio",
        type="enum",
        label="Aspect ratio",
        default="16:9",
        options=VIDEO_ASPECT_RATIOS,
        group="output",
    ),
    "resolution": ParameterSpec(
        name="resolution",
        type="enum",
        label="Resolution",
        default="720p",
        options=("480p", "720p", "768P", "1080p"),
        group="output",
    ),
    "duration": ParameterSpec(
        name="duration",
        type="enum",
        label="Duration",
        default="5",
        options=("5", "6", "8", "10"),
        group="output",
    ),
    "seconds": ParameterSpec(
        name="seconds",
        type="enum",
        label="Duration",
        default="4",
        options=("4", "8", "12", "16", "20"),
        group="output",
    ),
    "negative_prompt": _negative_prompt(),
    "seed": ParameterSpec(
        name="seed",
        type="integer",
        label="Seed",
        group="output",
        advanced=True,
    ),
    "generate_audio": ParameterSpec(
        name="generate_audio",
        type="boolean",
        label="Generate audio",
        description="This endpoint produces a soundtrack along with the picture.",
        default=True,
        group="output",
    ),
    "prompt_optimizer": ParameterSpec(
        name="prompt_optimizer",
        type="boolean",
        label="Let the provider rewrite the prompt",
        description="The provider optimises your text before generating. It changes the result, so it stays off unless you ask for it.",
        default=False,
        group="provider",
        advanced=True,
    ),
    "enable_prompt_expansion": ParameterSpec(
        name="enable_prompt_expansion",
        type="boolean",
        label="Let the provider expand the prompt",
        default=False,
        group="provider",
        advanced=True,
    ),
    "enable_safety_checker": _safety_checker(True),
    "auto_fix": ParameterSpec(
        name="auto_fix",
        type="boolean",
        label="Auto fix",
        description="The provider retries internally when its own check fails.",
        default=True,
        group="provider",
        advanced=True,
    ),
    "fps": ParameterSpec(
        name="fps",
        type="integer",
        label="Frames per second",
        default=25,
        minimum=1,
        maximum=60,
        group="output",
        advanced=True,
    ),
    "frames": ParameterSpec(
        name="frames",
        type="integer",
        label="Frame count",
        description="Total frames, which sets the clip length at the chosen frame rate.",
        default=121,
        minimum=8,
        group="output",
        advanced=True,
    ),
    "video_quality": ParameterSpec(
        name="video_quality",
        type="enum",
        label="Video quality",
        default="high",
        options=("low", "medium", "high"),
        group="quality",
        advanced=True,
    ),
}

#: A parameter for telling the camera what to do, attached only to endpoints whose
#: schema actually has the field. None of the endpoints documented in this
#: repository expose one, so camera moves are offered as prompt wording and are
#: labelled that way in the studio. The spec exists so a hand registered endpoint
#: that does document such a field can declare it without code changes.
NATIVE_CAMERA_PARAMETER = ParameterSpec(
    name="camera_move",
    type="enum",
    label="Camera move",
    description="A camera instruction this endpoint accepts as an API parameter rather than as prompt wording.",
    default="static",
    options=("static", "dolly_in", "dolly_out"),
    group="camera",
)


def spec(name: str, **overrides: Any) -> ParameterSpec:
    """Look up a documented spec by name and optionally adjust its bounds.

    Overrides exist because one parameter name carries different ranges on
    different endpoints -- qwen-image allows 250 steps where flux-2 allows 50 --
    and the UI must show the range that belongs to the endpoint in hand.
    """
    base = IMAGE_PARAMETERS.get(name) or VIDEO_PARAMETERS.get(name)
    if base is None:
        raise KeyError("no documented parameter named " + name)
    if not overrides:
        return base
    return replace(base, **overrides)
