"""Model adapters: one per provider protocol, one shared interface.

The rule this package enforces is simple. An adapter either performs a real
operation against a provider or raises a capability error saying it cannot. No
adapter fabricates a successful generation, a job id, an output URL, or a cost.
"""

from .base import (
    CAMERA_MOVES,
    CAMERA_MOVES_BY_KEY,
    IMAGE_MODES,
    MODE_LABELS,
    MODES_BY_MODALITY,
    MODES_REQUIRING_IMAGE,
    MODES_REQUIRING_MASK,
    MODES_REQUIRING_VIDEO,
    PARAMETER_TYPES,
    REFERENCE_ROLES,
    VIDEO_MODES,
    CameraMove,
    Modality,
    ParameterSchema,
    ParameterSpec,
    camera_move,
    describe_parameter_types,
    mode_label,
    modes_for,
    parse_parameter_schema,
)
# Importing the adapter modules is what runs their @register decorators. Without
# this the registry is empty and the wizard has nothing to offer, so the import
# is deliberate rather than a convenience.
from . import declarative as _declarative  # noqa: F401
from . import fal as _fal  # noqa: F401
from . import mock as _mock  # noqa: F401
from . import openai as _openai  # noqa: F401
from .core import adapter_types, adapter_for_model, get_adapter, known_adapters
from .types import (
    AdapterContext,
    CapabilityProfile,
    ConnectionTestResult,
    CostEstimate,
    DiscoveredModel,
    GenerationRequest,
    JobStatus,
    ProviderOutput,
    SubmittedJob,
)

__all__ = [
    "AdapterContext",
    "CAMERA_MOVES",
    "CAMERA_MOVES_BY_KEY",
    "CameraMove",
    "CapabilityProfile",
    "ConnectionTestResult",
    "CostEstimate",
    "DiscoveredModel",
    "GenerationRequest",
    "IMAGE_MODES",
    "JobStatus",
    "MODE_LABELS",
    "MODES_BY_MODALITY",
    "MODES_REQUIRING_IMAGE",
    "MODES_REQUIRING_MASK",
    "MODES_REQUIRING_VIDEO",
    "Modality",
    "PARAMETER_TYPES",
    "ParameterSchema",
    "ParameterSpec",
    "ProviderOutput",
    "REFERENCE_ROLES",
    "SubmittedJob",
    "VIDEO_MODES",
    "adapter_for_model",
    "adapter_types",
    "camera_move",
    "describe_parameter_types",
    "get_adapter",
    "known_adapters",
    "mode_label",
    "modes_for",
    "parse_parameter_schema",
]
