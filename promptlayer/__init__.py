"""Prompt Layer — see what a model provider adds to your prompt before you pay.

The dataset (`layers.json`) and the assembly logic (`assembler.py`) are the whole
product; every HTTP surface is a thin wrapper around them.

Honesty rule that the tests enforce: a layer is only ever reported from a public
source, with the source and the verbatim evidence attached. Unknown models return
`unknown_model: true` rather than a guessed default.
"""

from .assembler import (  # noqa: F401
    build_compile_request,
    build_preflight,
    dataset_version,
    expander_styles,
    model_detail,
    model_list,
    reload_layers,
)
from .improve import (  # noqa: F401
    apply_design,
    build_improve,
    check_selection_conflicts,
    clean_prompt,
    design_version,
    lint_prompt,
    load_design,
    reload_design,
    vocab,
)
from .presets import (  # noqa: F401
    hf_model_presets,
    hf_presets,
    preset_index,
    preset_languages,
    preset_source,
    preset_tasks,
    reload_presets,
    widget_example_sections,
    widget_example_slugs,
    widget_example_types,
    widget_examples,
)
from .reference import (  # noqa: F401
    MAX_IMAGES,
    apply_palette,
    build_reference_set,
    design_summary,
    reference_roles,
    reference_sheet,
    role_support,
)
from .storyboard import (  # noqa: F401
    build_scenes,
    continuity_sheet,
    facet_catalog,
    planner_rules,
    plan,
    read_facets,
    reload_storyboard,
    role_catalog,
    scene_params,
    split_beats,
    storyboard_version,
)
from .video import (  # noqa: F401
    build_plan,
    generation_gate,
    submit_plan,
)

__all__ = [
    "MAX_IMAGES",
    "apply_design",
    "apply_palette",
    "build_compile_request",
    "build_improve",
    "build_plan",
    "build_preflight",
    "build_reference_set",
    "build_scenes",
    "check_selection_conflicts",
    "clean_prompt",
    "continuity_sheet",
    "dataset_version",
    "design_summary",
    "design_version",
    "expander_styles",
    "facet_catalog",
    "generation_gate",
    "hf_model_presets",
    "hf_presets",
    "lint_prompt",
    "load_design",
    "model_detail",
    "model_list",
    "plan",
    "planner_rules",
    "preset_index",
    "preset_languages",
    "preset_source",
    "preset_tasks",
    "read_facets",
    "reference_roles",
    "reference_sheet",
    "reload_design",
    "reload_layers",
    "reload_presets",
    "reload_storyboard",
    "role_catalog",
    "role_support",
    "scene_params",
    "split_beats",
    "storyboard_version",
    "submit_plan",
    "vocab",
    "widget_example_sections",
    "widget_example_slugs",
    "widget_example_types",
    "widget_examples",
]
