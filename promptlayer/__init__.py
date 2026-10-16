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

__all__ = [
    "build_compile_request",
    "build_preflight",
    "dataset_version",
    "expander_styles",
    "model_detail",
    "model_list",
    "reload_layers",
]
