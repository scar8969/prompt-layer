"""The OpenAI images API, as its own adapter.

This exists as a registered provider type rather than only as a declarative
mapping preset so that the wizard can offer it by name and so that the small
number of things OpenAI does that a generic mapping cannot express are handled
in code:

* the response carries either a URL or inline base64 in `data[0].b64_json`, so
  the reader accepts both;
* `n` is a real parameter rather than a count field the user has to guess at;
* an edit is a multipart request against a different path, chosen by mode.

Everything else is the declarative engine with a fixed, trusted mapping, so this
file is deliberately thin: it is a preset plus three overrides, not a second
implementation of the protocol.

What has and has not been exercised: the request and response shapes below come
from the published API documentation and from the OpenAI-compatible servers this
shape is widely implemented by. No live OpenAI credential was available while
this was written, so no real image has been generated through it here. That is
recorded in docs/LIMITATIONS.md.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..errors import CAPABILITY_UNSUPPORTED, VALIDATION_FAILED, ApiError
from .core import register
from .declarative import (
    OPENAI_IMAGES_EDIT_SPEC,
    OPENAI_IMAGES_SPEC,
    DeclarativeHttpAdapter,
    load_spec,
    render_path,
)
from .types import AdapterContext, CapabilityProfile, ConnectionTestResult, DiscoveredModel

#: Modes the images endpoint can serve. Deliberately short: the API generates,
#: edits and varies. It does not inpaint with a mask on the generations route,
#: and claiming otherwise would switch on a studio button that cannot work.
SUPPORTED_MODES = ("text_to_image", "image_to_image", "variations")


@register
class OpenAIImagesAdapter(DeclarativeHttpAdapter):
    """The OpenAI images API, driven by a fixed mapping this platform trusts.

    Subclasses the declarative adapter so there is one implementation of
    "send a request, read a status, collect an output", and only the parts that
    are genuinely specific to this provider live here.
    """

    slug = "openai_images"
    label = "OpenAI-compatible images API"
    summary = "Any server implementing the OpenAI images API: generate, edit and vary."
    is_mock = False
    auth_schemes = ("bearer", "header")
    discovers_models = True
    supports_cancel = False
    credential_hint = "An API key from the provider account page. It is sent as a bearer token."

    def default_modes(self) -> Tuple[str, ...]:
        """Generating an image from text is the one mode this API always has."""
        return ("text_to_image",)

    def spec_for(self, context: AdapterContext) -> Dict[str, Any]:
        """The fixed mapping, unless the connection overrode it deliberately.

        A user who pointed this adapter at a compatible server with a slightly
        different path can supply their own mapping; the preset is the default,
        not a cage.
        """
        raw: Any = None
        if isinstance(context.config, dict):
            raw = context.config.get("mapping") or None
        if raw:
            return load_spec(raw)
        return load_spec(OPENAI_IMAGES_SPEC)

    def spec_for_mode(self, context: AdapterContext, mode: str) -> Dict[str, Any]:
        """The generations mapping, or the edits mapping for an edit mode."""
        raw: Any = None
        if isinstance(context.config, dict):
            raw = context.config.get("mapping") or None
        if raw:
            return load_spec(raw)
        if mode in ("image_to_image", "variations"):
            return load_spec(OPENAI_IMAGES_EDIT_SPEC)
        return load_spec(OPENAI_IMAGES_SPEC)

    def capabilities_for(
        self, context: AdapterContext, model_key: str
    ) -> Optional[CapabilityProfile]:
        """What the API documents for its image models, stated as a claim.

        These are the API capabilities, not a discovery: the list endpoint says
        nothing about modes. A user registering a model against a compatible
        server can narrow this on the Models screen.
        """
        return CapabilityProfile(
            modality="image",
            modes=SUPPORTED_MODES,
            aspect_ratios=("1:1", "3:2", "2:3"),
            resolutions=("1024x1024", "1536x1024", "1024x1536"),
            max_outputs=4,
            supports_negative_prompt=False,
            supports_seed=False,
            supports_mask=True,
            notes=(
                "The images API documents size, n and quality. It has no negative prompt and no seed field.",
            ),
        )

    def submit(self, context: AdapterContext, request: Any):
        """Submit through the mapping that matches this request mode."""
        if request.mode not in SUPPORTED_MODES:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "The OpenAI images API does not offer %r." % request.mode,
                details={"mode": request.mode, "supported": list(SUPPORTED_MODES)},
                hint="Choose text to image, image to image or a variation.",
            )
        if request.mode in ("image_to_image", "variations") and not request.references:
            raise ApiError(
                VALIDATION_FAILED,
                "This mode needs a source image.",
                field="references",
            )
        original = self.spec_for
        mode = request.mode
        override = self.spec_for_mode(context, mode)

        # The base submit reads its mapping through spec_for, so the chosen
        # mapping is installed for the duration of this call instead of being
        # threaded through every helper.
        def _installed(_context: AdapterContext) -> Dict[str, Any]:
            return override

        self.spec_for = _installed  # type: ignore[assignment]
        try:
            return super().submit(context, request)
        finally:
            self.spec_for = original  # type: ignore[assignment]

    def test_connection(self, context: AdapterContext) -> ConnectionTestResult:
        """List the models route, which is the cheapest proof the key works."""
        return super().test_connection(context)

    def list_models(self, context: AdapterContext) -> Tuple[DiscoveredModel, ...]:
        """The provider model list, with no modality claimed.

        The list endpoint does not say which entries generate images, so a
        discovered entry is registered as "other" and the user declares what it
        is. That is slower and honest, rather than fast and wrong.
        """
        return super().list_models(context)
