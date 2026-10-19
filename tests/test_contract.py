"""The client and the server must agree on every route.

An inert button is not usually a missing component: it is a component calling a
path the server does not serve. This test reads the literal route strings out of
`web/src/api/endpoints.ts` and fails when the running application has no such
route, so "the UI calls something that does not exist" breaks the build instead
of shipping.

It reads the source rather than rendering the client, so it needs no browser and
no network. What it cannot catch: a control that calls a real route with the
wrong body. `tests/test_studio.py` covers the bodies of the routes that matter.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402

ENDPOINTS = os.path.join(REPO, "web", "src", "api", "endpoints.ts")


@pytest.fixture(scope="module")
def route_table():
    """The declared route table, from a real application build."""
    config = load_config(
        {
            "STUDIO_DATA_DIR": tempfile.mkdtemp(prefix="studio-contract-"),
            "STUDIO_ENV": "development",
            "STUDIO_ENCRYPTION_KEY": "a" * 64,
            "STUDIO_AUTH_SECRET": "b" * 40,
        }
    )
    app = build_app(config)
    try:
        yield app.router.describe()
    finally:
        app.shutdown()


def client_paths() -> dict:
    """Every /api/... literal in the client, normalised to a route template.

    A template literal such as `/api/jobs/${id}/events` becomes
    `/api/jobs/{id}/events`, which is exactly the shape the router stores.
    """
    with open(ENDPOINTS, encoding="utf-8") as handle:
        source = handle.read()
    found = {}
    for match in re.finditer(r"`(/api/[^`]+)`", source):
        raw = match.group(1)
        template = re.sub(r"\$\{[^}]+\}", "{id}", raw)
        template = template.split("?")[0].rstrip("/") or "/"
        found[template] = found.get(template, 0) + 1
    return found


def test_the_client_calls_only_routes_the_server_serves(route_table):
    served = {item["path"] for item in route_table}
    called = client_paths()
    assert called, "no /api/ literals found; the endpoint module changed shape"
    missing = sorted(path for path in called if path not in served)
    assert missing == [], "the client calls routes that are not served: %r" % (missing,)


def test_every_call_site_finds_a_method(route_table):
    by_path = {}
    for item in route_table:
        by_path.setdefault(item["path"], set()).add(item["method"])
    for path in client_paths():
        assert by_path.get(path), "no method registered for %s" % path


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/auth/register", "POST"),
        ("/api/auth/login", "POST"),
        ("/api/auth/me", "GET"),
        ("/api/system/status", "GET"),
        ("/api/connections", "GET"),
        ("/api/connections", "POST"),
        ("/api/connections/{id}/test", "POST"),
        ("/api/connections/{id}/rotate-key", "POST"),
        ("/api/connections/{id}/models", "POST"),
        ("/api/models/{id}/capabilities", "GET"),
        ("/api/models/{id}/parameters", "GET"),
        ("/api/generations/images", "POST"),
        ("/api/generations/videos", "POST"),
        ("/api/generations/{id}/variations", "POST"),
        ("/api/jobs/{id}/cancel", "POST"),
        ("/api/jobs/{id}/retry", "POST"),
        ("/api/assets/{id}/content", "GET"),
        ("/api/assets/{id}/thumbnail", "GET"),
        ("/api/presets", "GET"),
        ("/api/prompts", "POST"),
        ("/api/characters", "POST"),
        ("/api/usage", "GET"),
    ],
)
def test_a_critical_route_exists_with_the_method_the_client_uses(route_table, path, method):
    matches = [item for item in route_table if item["path"] == path]
    assert [item["method"] for item in matches], "no route declared for %s" % path
    assert method in [item["method"] for item in matches], (
        "%s is declared for %s, but the client calls it with %s"
        % (path, [item["method"] for item in matches], method),
    )


def test_no_route_is_declared_twice(route_table):
    seen = {}
    for item in route_table:
        key = (item["method"], item["path"])
        assert key not in seen, "two handlers registered for %s %s" % (item["method"], item["path"])
        seen[key] = item["name"]


def test_only_the_public_routes_skip_the_session_check(route_table):
    """Six routes are reachable without a session, and each has a stated reason.

    Registration, sign-in, logout, password reset and its two halves are the
    whole list. Everything else -- every read of workspace data, every write --
    requires a resolved session, so forgetting to authenticate a route is a
    test failure rather than a silent hole.
    """
    public = sorted(
        "%s %s" % (item["method"], item["path"])
        for item in route_table
        if not item["authenticated"]
    )
    assert public == [
        "GET /api/health",
        "GET /api/system/status",
        "POST /api/auth/forgot-password",
        "POST /api/auth/login",
        # Logout stays public so a browser holding a stale cookie can clear it
        # and get a 200 rather than an error it cannot act on.
        "POST /api/auth/logout",
        "POST /api/auth/register",
        "POST /api/auth/reset-password",
    ], public
