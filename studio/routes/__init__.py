"""The HTTP route table. One registration function, called once by build_app."""

from __future__ import annotations

from typing import Any

from . import assets, auth, connections, generations, jobs, library, models, system


def register_routes(router: Any, app: Any) -> None:
    """Attach every route group to the router.

    Order does not matter: the router resolves by pattern, not by declaration
    order, and a path that exists under another verb is answered 405 rather than
    404. Each group owns its own paths so a duplicate registration would fail
    loudly here rather than silently shadow a handler.
    """
    for module in (system, auth, connections, models, generations, jobs, assets, library):
        module.register_routes(router, app)


__all__ = ["register_routes"]
