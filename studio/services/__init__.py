"""Application services. Each one owns a real operation, not a layer of indirection.

connections.py   credential handling, egress validation, connection tests, model registry
generations.py   turning a studio request into a queued job, and the derived operations
runner.py        the worker loop that drives a job to a terminal state
assets.py        importing provider output into object storage and the asset index
assistant.py     prompt rewriting by fixed rules, with nothing sent to a provider
"""

from __future__ import annotations
