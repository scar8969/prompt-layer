"""What each documented fal.ai endpoint can actually do.

Every entry was assembled from a document in this repository rather than from
memory: the image entries from promptlayer/vendor/fal/*.json (fal's own OpenAPI
files) and the video entries from the parameter tables and source citations in
promptlayer/layers.json. The endpoint identifiers were additionally checked
against the public catalog at https://api.fal.ai/v1/models.

The table answers one question honestly for each endpoint: which of this
platform's studio modes does it really serve, and which does it not? An endpoint
whose schema has no mask field cannot inpaint. An endpoint whose schema has no
ending-frame field cannot take a last frame. Both facts are recorded as
restrictions that name the endpoint which does support the mode, so the studio
disables that button and explains where to go instead of failing after a
generation has already been submitted.

Everything here is a starting point for a deployment, not a boundary around it.
fal lists hundreds of endpoints and adds more; list_models discovers whatever the
account can see, and a user can register any endpoint by hand. A hand registered
endpoint gets a deliberately narrow profile, because this platform has not read
its schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import catalog
from .base import ParameterSchema
from .catalog import spec
from .types import CapabilityProfile


@dataclass(frozen=True)
class EndpointFacts:
    """One endpoint: how to reach it, what it accepts, what it can produce."""

    key: str
    display_name: str
    modality: str
    modes: Tuple[str, ...]
    image_field: Optional[str] = None
    image_field_is_list: bool = False
    last_frame_field: Optional[str] = None
    mask_field: Optional[str] = None
    max_reference_images: int = 0
    aspect_ratios: Tuple[str, ...] = ()
    resolutions: Tuple[str, ...] = ()
    durations_seconds: Tuple[float, ...] = ()
    frame_rates: Tuple[float, ...] = ()
    reference_roles: Tuple[str, ...] = ()
    supports_negative_prompt: bool = False
    supports_seed: bool = False
    supports_audio: bool = False
    supports_mask: bool = False
    native_camera_moves: Tuple[str, ...] = ()
    max_outputs: int = 1
    restrictions: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    parameters: Tuple[Any, ...] = ()
    source: str = ""

    @property
    def requires_image(self) -> bool:
        return self.image_field is not None

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            modality=self.modality,
            modes=self.modes,
            aspect_ratios=self.aspect_ratios,
            resolutions=self.resolutions,
            durations_seconds=self.durations_seconds,
            frame_rates=self.frame_rates,
            reference_roles=self.reference_roles,
            max_reference_images=self.max_reference_images,
            max_outputs=self.max_outputs,
            supports_negative_prompt=self.supports_negative_prompt,
            supports_seed=self.supports_seed,
            supports_audio=self.supports_audio,
            supports_mask=self.supports_mask,
            native_camera_moves=self.native_camera_moves,
            restrictions=self.restrictions,
            notes=self.notes,
        )

    def schema(self) -> ParameterSchema:
        return ParameterSchema(specs=list(self.parameters))

    def to_public(self) -> Dict[str, Any]:
        return {
            "model_key": self.key,
            "display_name": self.display_name,
            "modality": self.modality,
            "modes": list(self.modes),
            "requires_image": self.requires_image,
            "restrictions": list(self.restrictions),
            "source": self.source,
        }


NO_MASK_NOTE = (
    "This endpoint has no mask field, so masked inpainting and canvas expansion are unavailable on it."
)

_IMAGE_EDIT_RESTRICTIONS: Tuple[str, ...] = (
    NO_MASK_NOTE + " It edits the whole image from a text instruction instead.",
)


# ---------------------------------------------------------------------------
# Image endpoints, transcribed from the vendored OpenAPI documents
# ---------------------------------------------------------------------------

IMAGE_ENDPOINTS: Tuple[EndpointFacts, ...] = (
    EndpointFacts(
        key="fal-ai/flux-2",
        display_name="FLUX.2 [dev]",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=catalog.IMAGE_SIZE_PRESETS,
        supports_seed=True,
        max_outputs=4,
        restrictions=(
            "Takes no input image. To edit or restyle an existing picture use fal-ai/flux-2/edit.",
        ),
        notes=("Black Forest Labs FLUX.2 [dev] with configurable step count.",),
        parameters=(
            spec("num_images", maximum=4),
            spec("image_size"),
            spec("output_format"),
            spec("seed"),
            spec("num_inference_steps", default=28, minimum=4, maximum=50),
            spec("guidance_scale", default=2.5),
            spec("acceleration"),
            spec("enable_prompt_expansion"),
            spec("enable_safety_checker"),
        ),
        source="promptlayer/vendor/fal/fal-ai_flux-2.json",
    ),
    EndpointFacts(
        key="fal-ai/flux-2/edit",
        display_name="FLUX.2 [dev] Edit",
        modality="image",
        modes=("image_to_image", "reference_guided", "multi_reference", "style_transfer", "batch"),
        image_field="image_urls",
        image_field_is_list=True,
        max_reference_images=10,
        reference_roles=("main_subject", "composition", "style", "environment", "product", "color_palette"),
        supports_seed=True,
        max_outputs=4,
        restrictions=_IMAGE_EDIT_RESTRICTIONS,
        notes=("The schema accepts up to ten input images in one request.",),
        parameters=(
            spec("num_images", maximum=4),
            spec("image_size"),
            spec("output_format"),
            spec("seed"),
            spec("num_inference_steps", default=28, minimum=4, maximum=50),
            spec("guidance_scale", default=2.5),
            spec("acceleration"),
            spec("enable_prompt_expansion"),
            spec("enable_safety_checker"),
        ),
        source="promptlayer/vendor/fal/fal-ai_flux-2_edit.json",
    ),
    EndpointFacts(
        key="fal-ai/flux-2-pro",
        display_name="FLUX.2 [pro]",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=catalog.IMAGE_SIZE_PRESETS,
        supports_seed=True,
        restrictions=(
            "Takes no input image and has no mask field. Use fal-ai/flux-2/edit for editing.",
        ),
        notes=(
            "The pro endpoint exposes no step count or guidance control; the provider manages sampling.",
        ),
        parameters=(
            spec("image_size"),
            spec("output_format", default="jpeg", options=("jpeg", "png")),
            spec("seed"),
            spec("safety_tolerance", default="2", options=("1", "2", "3", "4", "5")),
            spec("enable_safety_checker"),
        ),
        source="promptlayer/vendor/fal/fal-ai_flux-2-pro.json",
    ),
    EndpointFacts(
        key="fal-ai/nano-banana",
        display_name="Nano Banana",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=("auto",) + catalog.ASPECT_RATIOS,
        supports_seed=True,
        max_outputs=4,
        restrictions=(
            "Takes no input image. To modify a picture use fal-ai/nano-banana/edit.",
        ),
        notes=("Sizes are chosen by aspect ratio rather than by named frame.",),
        parameters=(
            spec("aspect_ratio", default="1:1", options=("auto",) + catalog.ASPECT_RATIOS),
            spec("num_images", maximum=4),
            spec("output_format"),
            spec("seed"),
            spec("safety_tolerance"),
            spec("limit_generations"),
            spec("sync_mode"),
        ),
        source="promptlayer/vendor/fal/fal-ai_nano-banana.json",
    ),
    EndpointFacts(
        key="fal-ai/nano-banana/edit",
        display_name="Nano Banana Edit",
        modality="image",
        modes=("image_to_image", "reference_guided", "multi_reference", "style_transfer", "batch"),
        image_field="image_urls",
        image_field_is_list=True,
        max_reference_images=10,
        reference_roles=("main_subject", "composition", "style", "environment", "product", "color_palette"),
        supports_seed=True,
        max_outputs=4,
        restrictions=_IMAGE_EDIT_RESTRICTIONS,
        notes=("Aspect ratio defaults to auto, which follows the input image's proportions.",),
        parameters=(
            spec("aspect_ratio", default="auto", options=("auto",) + catalog.ASPECT_RATIOS),
            spec("num_images", maximum=4),
            spec("output_format"),
            spec("seed"),
            spec("safety_tolerance"),
            spec("limit_generations"),
            spec("sync_mode"),
        ),
        source="promptlayer/vendor/fal/fal-ai_nano-banana_edit.json",
    ),
    EndpointFacts(
        key="fal-ai/gemini-25-flash-image",
        display_name="Gemini 2.5 Flash Image",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=("auto",) + catalog.ASPECT_RATIOS,
        supports_seed=True,
        max_outputs=4,
        restrictions=(
            "The schema exposes no image input field, so this platform cannot submit an edit here.",
        ),
        notes=(
            "The response can carry a text description of the result, which is stored alongside the image.",
        ),
        parameters=(
            spec("aspect_ratio", options=("auto",) + catalog.ASPECT_RATIOS),
            spec("num_images", maximum=4),
            spec("output_format"),
            spec("seed"),
            spec("safety_tolerance"),
            spec("limit_generations"),
            spec("sync_mode"),
        ),
        source="promptlayer/vendor/fal/fal-ai_gemini-25-flash-image.json",
    ),
    EndpointFacts(
        key="fal-ai/qwen-image",
        display_name="Qwen Image",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=catalog.IMAGE_SIZE_PRESETS,
        supports_negative_prompt=True,
        supports_seed=True,
        max_outputs=4,
        restrictions=(
            "Takes no input image. Use fal-ai/qwen-image/edit, which accepts one image.",
        ),
        notes=(
            "One of only two endpoints in this table whose schema has a negative prompt field.",
        ),
        parameters=(
            spec("image_size"),
            spec("negative_prompt"),
            spec("num_images", maximum=4),
            spec("num_inference_steps", default=30, minimum=2, maximum=250),
            spec("guidance_scale", default=2.5),
            spec("acceleration", default="none"),
            spec("use_turbo"),
            spec("output_format", options=("jpeg", "png")),
            spec("seed"),
            spec("enable_safety_checker"),
        ),
        source="promptlayer/vendor/fal/fal-ai_qwen-image.json",
    ),
    EndpointFacts(
        key="fal-ai/qwen-image/edit",
        display_name="Qwen Image Edit",
        modality="image",
        modes=("image_to_image", "reference_guided", "style_transfer"),
        image_field="image_url",
        image_field_is_list=False,
        max_reference_images=1,
        reference_roles=("main_subject", "composition", "style"),
        supports_negative_prompt=True,
        supports_seed=True,
        max_outputs=4,
        restrictions=(
            "One input image, and no mask field, so masked inpainting is unavailable here. "
            + NO_MASK_NOTE
        ),
        notes=("The field is image_url, singular, unlike the other edit endpoints' image_urls.",),
        parameters=(
            spec("negative_prompt", default=" "),
            spec("num_images", maximum=4),
            spec("num_inference_steps", default=30, minimum=2, maximum=50),
            spec("guidance_scale", default=4.0),
            spec("acceleration"),
            spec("image_size", default=None),
            spec("output_format", options=("jpeg", "png")),
            spec("seed"),
            spec("enable_safety_checker"),
        ),
        source="promptlayer/vendor/fal/fal-ai_qwen-image_edit.json",
    ),
    EndpointFacts(
        key="fal-ai/bytedance/seedream/v4/text-to-image",
        display_name="Seedream 4.0",
        modality="image",
        modes=("text_to_image", "batch"),
        aspect_ratios=catalog.SEEDREAM_SIZES,
        resolutions=("1K", "2K", "4K"),
        supports_seed=True,
        max_outputs=6,
        restrictions=(
            "Takes no input image. Use fal-ai/bytedance/seedream/v4/edit for editing.",
        ),
        notes=(
            "Size accepts a named frame, an auto preset, or explicit pixel dimensions up to 14142 per side.",
        ),
        parameters=(
            spec("num_images", maximum=6),
            spec("image_size", default=None, options=catalog.SEEDREAM_SIZES),
            spec("max_images", maximum=6),
            spec("enhance_prompt_mode"),
            spec("seed"),
            spec("enable_safety_checker"),
            spec("sync_mode"),
        ),
        source="promptlayer/vendor/fal/fal-ai_bytedance_seedream_v4_text-to-image.json",
    ),
    EndpointFacts(
        key="fal-ai/bytedance/seedream/v4/edit",
        display_name="Seedream 4.0 Edit",
        modality="image",
        modes=("image_to_image", "reference_guided", "multi_reference", "batch"),
        image_field="image_urls",
        image_field_is_list=True,
        max_reference_images=10,
        reference_roles=("main_subject", "composition", "product", "environment", "color_palette", "style"),
        resolutions=("1K", "2K", "4K"),
        supports_seed=True,
        max_outputs=6,
        restrictions=_IMAGE_EDIT_RESTRICTIONS,
        notes=("The schema describes up to ten image inputs per request.",),
        parameters=(
            spec("num_images", maximum=6),
            spec("image_size", default=None, options=catalog.SEEDREAM_SIZES),
            spec("max_images", maximum=6),
            spec("enhance_prompt_mode"),
            spec("seed"),
            spec("enable_safety_checker"),
            spec("sync_mode"),
        ),
        source="promptlayer/vendor/fal/fal-ai_bytedance_seedream_v4_edit.json",
    ),
)


# ---------------------------------------------------------------------------
# Video endpoints, transcribed from promptlayer/layers.json
# ---------------------------------------------------------------------------


def _v(*pairs: Tuple[str, Dict[str, Any]]) -> Tuple[Any, ...]:
    return tuple(spec(name, **overrides) for name, overrides in pairs)


_T2V_COMMON = (
    ("aspect_ratio", {}),
    ("resolution", {}),
    ("duration", {}),
    ("enable_safety_checker", {}),
)

VIDEO_ENDPOINTS: Tuple[EndpointFacts, ...] = (
    EndpointFacts(
        key="fal-ai/veo3/fast",
        display_name="Veo 3 Fast",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16"),
        resolutions=("720p", "1080p"),
        durations_seconds=(4.0, 6.0, 8.0),
        frame_rates=(24.0,),
        supports_negative_prompt=True,
        supports_seed=True,
        supports_audio=True,
        native_camera_moves=(),
        restrictions=(
            "No input image on this endpoint, so image-to-video and first-frame control are unavailable. "
            "For a clip driven by a still, use fal-ai/minimax/hailuo-02/standard/image-to-video.",
        ),
        notes=(
            "Produces its own audio track. The studio labels audio-enabled endpoints because the soundtrack is part of the output, not an optional extra.",
        ),
        parameters=_v(
            ("aspect_ratio", {"default": "16:9", "options": ("16:9", "9:16")}),
            ("resolution", {"default": "720p", "options": ("720p", "1080p")}),
            ("duration", {"default": "8", "options": ("4", "6", "8")}),
            ("generate_audio", {}),
            ("auto_fix", {}),
            ("safety_tolerance", {"options": ("1", "2", "3", "4", "5", "6")}),
            ("negative_prompt", {}),
            ("seed", {}),
        ),
        source="promptlayer/layers.json (models.veo3-fast)",
    ),
    EndpointFacts(
        key="fal-ai/minimax/hailuo-02/standard/text-to-video",
        display_name="Hailuo 02 Standard",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("512P", "768P", "1080P"),
        durations_seconds=(6.0, 10.0),
        restrictions=(
            "Takes no input image. The image-driven variant of the same model is fal-ai/minimax/hailuo-02/standard/image-to-video.",
        ),
        parameters=_v(
            ("duration", {"default": "6", "options": ("6", "10")}),
            ("prompt_optimizer", {"default": True}),
            ("enable_safety_checker", {}),
        ),
        source="promptlayer/layers.json (models.hailuo-02-standard)",
    ),
    EndpointFacts(
        key="fal-ai/minimax/hailuo-02/pro/text-to-video",
        display_name="Hailuo 02 Pro",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("512P", "768P", "1080P"),
        durations_seconds=(6.0, 10.0),
        restrictions=(
            "Takes no input image, and this platform has no documented first-frame variant for the pro tier.",
        ),
        parameters=_v(
            ("duration", {"default": "6", "options": ("6", "10")}),
            ("prompt_optimizer", {"default": True}),
        ),
        source="promptlayer/layers.json (models.hailuo-02-pro)",
    ),
    EndpointFacts(
        key="fal-ai/minimax/hailuo-02/standard/image-to-video",
        display_name="Hailuo 02 Standard (image to video)",
        modality="video",
        modes=("image_to_video", "first_frame", "first_last_frame"),
        image_field="image_url",
        image_field_is_list=False,
        last_frame_field="end_image_url",
        max_reference_images=1,
        reference_roles=("first_frame", "last_frame"),
        resolutions=("512P", "768P"),
        durations_seconds=(6.0, 10.0),
        restrictions=(
            "Movement follows the supplied frame; this endpoint does not invent a first frame from text alone.",
            "10 second clips are unavailable at 1080p; the provider does not offer 1080p on this endpoint at all, "
            "so the studio lists 512P and 768P.",
        ),
        notes=(
            "Field names taken from the endpoint's own OpenAPI document: the input image is image_url and the "
            "optional closing frame is end_image_url. The provider's own schema is authoritative here and is "
            "preferred at runtime; this entry exists so a deployment with no outbound access still gets the "
            "same answer.",
        ),
        parameters=_v(
            ("resolution", {"default": "768P", "options": ("512P", "768P")}),
            ("duration", {"default": "6", "options": ("6", "10")}),
            ("prompt_optimizer", {"default": True}),
        ),
        source="fal.ai queue OpenAPI document for this endpoint (cross-checked against promptlayer/layers.json, models.hailuo-02-i2v)",
    ),
    EndpointFacts(
        key="fal-ai/ltx-2-19b/text-to-video",
        display_name="LTX-2 19B",
        modality="video",
        modes=("text_to_video",),
        resolutions=("720p", "1080p"),
        frame_rates=(25.0,),
        supports_audio=True,
        restrictions=(
            "Text-driven only; this platform has no documented image conditioning endpoint for this model.",
        ),
        notes=(
            "Exposes a frame count rather than a duration in seconds, so the studio shows frames and reports the resulting length at the chosen frame rate.",
        ),
        parameters=_v(
            ("frames", {}),
            ("fps", {}),
            ("enable_prompt_expansion", {}),
            ("enable_safety_checker", {}),
            ("generate_audio", {}),
            ("video_quality", {}),
            ("num_inference_steps", {"default": 40, "minimum": 4, "maximum": 100}),
            ("guidance_scale", {"default": 3.0}),
        ),
        source="promptlayer/layers.json (models.ltx-2-19b)",
    ),
    EndpointFacts(
        key="fal-ai/kling-video/v2.1/master/text-to-video",
        display_name="Kling 2.1 Master",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("720p", "1080p"),
        durations_seconds=(5.0, 10.0),
        supports_negative_prompt=True,
        max_outputs=1,
        restrictions=(
            "Takes no input image on this endpoint; the same model's image-driven route is a separate endpoint in fal's catalog.",
        ),
        parameters=_v(
            ("duration", {"default": "5", "options": ("5", "10")}),
            ("aspect_ratio", {}),
            ("negative_prompt", {"default": "blur, distort, and low quality"}),
        ),
        source="promptlayer/layers.json (models.kling-v2.1-master)",
    ),
    EndpointFacts(
        key="fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
        display_name="Kling 2.5 Turbo Pro",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("720p", "1080p"),
        durations_seconds=(5.0, 10.0),
        supports_negative_prompt=True,
        restrictions=(
            "Takes no input image on this endpoint.",
        ),
        parameters=_v(
            ("duration", {"default": "5", "options": ("5", "10")}),
            ("aspect_ratio", {}),
            ("negative_prompt", {"default": "blur, distort, and low quality"}),
        ),
        source="promptlayer/layers.json (models.kling-v2.5-turbo-pro)",
    ),
    EndpointFacts(
        key="fal-ai/pika/v2.2/text-to-video",
        display_name="Pika 2.2",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("720p", "1080p"),
        durations_seconds=(5.0, 10.0),
        supports_negative_prompt=True,
        restrictions=(
            "Takes no input image on this endpoint.",
        ),
        parameters=_v(
            ("duration", {"default": "5", "options": ("5", "10")}),
            ("resolution", {}),
            ("aspect_ratio", {}),
            ("negative_prompt", {"default": "ugly, bad, terrible"}),
        ),
        source="promptlayer/layers.json (models.pika-v2.2)",
    ),
    EndpointFacts(
        key="fal-ai/bytedance/seedance/v1/pro/text-to-video",
        display_name="Seedance 1.0 Pro",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("480p", "720p", "1080p"),
        durations_seconds=(5.0, 10.0),
        restrictions=(
            "Takes no input image on this endpoint.",
        ),
        parameters=_v(
            ("aspect_ratio", {}),
            ("resolution", {"default": "1080p", "options": ("480p", "720p", "1080p")}),
            ("duration", {"default": "5", "options": ("5", "10")}),
            ("enable_safety_checker", {}),
        ),
        source="promptlayer/layers.json (models.seedance-v1-pro)",
    ),
    EndpointFacts(
        key="fal-ai/bytedance/seedance/v1/lite/text-to-video",
        display_name="Seedance 1.0 Lite",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("480p", "720p", "1080p"),
        durations_seconds=(5.0, 10.0),
        restrictions=(
            "Takes no input image on this endpoint.",
        ),
        parameters=_v(
            ("aspect_ratio", {}),
            ("resolution", {"default": "720p", "options": ("480p", "720p", "1080p")}),
            ("duration", {"default": "5", "options": ("5", "10")}),
            ("enable_safety_checker", {}),
        ),
        source="promptlayer/layers.json (models.seedance-v1-lite)",
    ),
    EndpointFacts(
        key="fal-ai/wan/v2.2-a14b/text-to-video",
        display_name="Wan 2.2 A14B",
        modality="video",
        modes=("text_to_video",),
        aspect_ratios=("16:9", "9:16", "1:1"),
        resolutions=("480p", "720p"),
        durations_seconds=(5.0,),
        supports_negative_prompt=True,
        supports_seed=True,
        restrictions=(
            "Takes no input image on this endpoint; fal-ai/wan/v2.2-a14b/image-to-video is the conditioning variant.",
        ),
        notes=("Prompt expansion is available but off by default on this endpoint.",),
        parameters=_v(
            ("enable_prompt_expansion", {}),
            ("resolution", {"default": "720p", "options": ("480p", "720p")}),
            ("aspect_ratio", {}),
            ("negative_prompt", {}),
            ("seed", {}),
        ),
        source="promptlayer/layers.json (models.wan-2.2-a14b)",
    ),
    EndpointFacts(
        key="fal-ai/wan/v2.2-a14b/image-to-video",
        display_name="Wan 2.2 A14B (image to video)",
        modality="video",
        modes=("image_to_video", "first_frame"),
        image_field="image",
        image_field_is_list=False,
        max_reference_images=1,
        reference_roles=("first_frame",),
        aspect_ratios=("auto",),
        resolutions=("480p", "720p"),
        durations_seconds=(5.0,),
        supports_negative_prompt=True,
        supports_seed=True,
        restrictions=(
            "One conditioning frame and no ending frame field, so this endpoint cannot interpolate between two frames.",
        ),
        notes=("The aspect ratio defaults to auto, following the input frame.",),
        parameters=_v(
            ("enable_prompt_expansion", {}),
            ("resolution", {"default": "720p", "options": ("480p", "720p")}),
            ("aspect_ratio", {"default": "auto", "options": ("auto",)}),
            ("negative_prompt", {}),
            ("seed", {}),
        ),
        source="promptlayer/layers.json (models.wan-2.2-a14b-i2v)",
    ),
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

ENDPOINTS: Tuple[EndpointFacts, ...] = IMAGE_ENDPOINTS + VIDEO_ENDPOINTS

ENDPOINTS_BY_KEY: Dict[str, EndpointFacts] = {facts.key: facts for facts in ENDPOINTS}


def endpoint(key: str) -> Optional[EndpointFacts]:
    return ENDPOINTS_BY_KEY.get(key)


def known_keys() -> Tuple[str, ...]:
    return tuple(facts.key for facts in ENDPOINTS)


def documented_keys_for_mode(mode: str) -> Tuple[str, ...]:
    """Which documented endpoints can serve a studio mode.

    The studio uses this to answer the user's real question -- "so which model
    should I pick for outpainting?" -- instead of only disabling a button.
    """
    return tuple(facts.key for facts in ENDPOINTS if mode in facts.modes)


def modes_without_documented_support(modality: Optional[str] = None) -> Dict[str, Tuple[str, ...]]:
    """Studio modes in the vocabulary that no documented endpoint serves.

    Reporting this list is the honest alternative to showing a control that would
    fail at submit time. The API surfaces it so the client can explain the gap.
    """
    from .base import IMAGE_MODES as _IMAGE_MODES, VIDEO_MODES as _VIDEO_MODES, modes_for

    covered: Dict[str, Tuple[str, ...]] = {}
    wanted = _IMAGE_MODES if modality == "image" else _VIDEO_MODES if modality == "video" else tuple(_IMAGE_MODES) + tuple(_VIDEO_MODES)
    for mode in wanted:
        if not documented_keys_for_mode(mode):
            covered[mode] = ()
    return covered


def unread_profile(key: str, modality: str = "other", *, notes: Tuple[str, ...] = ()) -> CapabilityProfile:
    """The profile given to an endpoint this platform has not read a schema for.

    It is deliberately narrow: a display name and a modality, and nothing else.
    The alternative -- guessing aspect ratios and modes from the modality label --
    is how a platform ends up sending an unsupported parameter to a paid
    endpoint. A user can widen this by hand in the model editor, and that
    widening is a deliberate, recorded action rather than an inference.
    """
    message = (
        "This model was registered by hand or discovered from the provider's catalog, so its "
        "interface has not been read. No modes, sizes or reference roles are assumed. "
        "Edit the model to declare what it supports."
    )
    return CapabilityProfile(
        modality=modality if modality in ("image", "video", "other") else "other",
        modes=(),
        notes=(message,) + tuple(notes),
        restrictions=(message,),
    )


def profile_for(key: str, modality: str = "image") -> CapabilityProfile:
    facts = endpoint(key)
    if facts is not None:
        return facts.capabilities()
    return unread_profile(key, modality)


def schema_for(key: str) -> ParameterSchema:
    facts = endpoint(key)
    if facts is not None:
        return facts.schema()
    return ParameterSchema()


def describe_coverage() -> Dict[str, Any]:
    """A factual summary for the models page footer."""
    image_modes = sorted({mode for facts in IMAGE_ENDPOINTS for mode in facts.modes})
    video_modes = sorted({mode for facts in VIDEO_ENDPOINTS for mode in facts.modes})
    return {
        "documented_endpoints": len(ENDPOINTS),
        "image_endpoints": len(IMAGE_ENDPOINTS),
        "video_endpoints": len(VIDEO_ENDPOINTS),
        "image_modes_covered": image_modes,
        "video_modes_covered": video_modes,
        "uncovered_modes": sorted(modes_without_documented_support()),
        "sources": [catalog.SCHEMA_SOURCE, "https://api.fal.ai/v1/models"],
    }
