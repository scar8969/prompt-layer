"""Phase 2: the serverless bridge serves the same application, without a socket.

The risk this suite addresses is drift. A second entrypoint that reimplements
routing, auth or error handling would pass its own tests and still disagree with
the process the maintainer runs locally. So these tests assert the property that
matters: a request built by `make_request` and handed to `HttpApp.handle` answers
exactly as the socket server does, with the same envelopes, headers and statuses.

No socket is opened anywhere in this file. `build_app` is used directly, which is
what `api/studio.py` does under Vercel.

The Postgres-backed cases skip without `STUDIO_TEST_POSTGRES`, like the other
backend suites.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.app import build_app  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.serverless import (  # noqa: E402
    application_path,
    client_ip_from,
    handle,
    reset_application,
)


@pytest.fixture(scope="module")
def studio_app(studio_database, tmp_path_factory):
    """A real application, request-scoped, no worker threads."""
    data_dir = str(tmp_path_factory.mktemp("serverless"))
    config = load_config(
        {
            "STUDIO_DATA_DIR": data_dir,
            "STUDIO_ENV": "development",
            "STUDIO_ENCRYPTION_KEY": "a" * 64,
            "STUDIO_AUTH_SECRET": "b" * 40,
            "STUDIO_ENABLE_MOCK_PROVIDER": "1",
            "STUDIO_PUBLIC_APP_URL": "http://localhost",
            "STUDIO_API_BASE_URL": "http://localhost",
            "STUDIO_DATABASE_URL": studio_database,
            "STUDIO_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_CREDENTIAL_RATE_LIMIT_PER_MINUTE": "100000",
            "STUDIO_JOB_WORKERS": "0",
        }
    )
    app = build_app(config, start_worker=False)
    yield app
    app.shutdown()
    reset_application()


def call(app, method, path, body=None, headers=None):
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    merged = {"host": "localhost", "accept": "application/json"}
    if body is not None:
        merged["content-type"] = "application/json"
    merged.update(headers or {})
    return handle(method=method, path=path, headers=merged, body=payload, app=app)


class TestTheBridgeAnswersLikeTheServer:
    def test_health_is_200(self, studio_app):
        response = call(studio_app, "GET", "/api/health")
        assert response.status == 200
        assert json.loads(response.body)["ok"] is True

    def test_an_unknown_route_is_a_json_404_envelope(self, studio_app):
        response = call(studio_app, "GET", "/api/nope")
        assert response.status == 404
        payload = json.loads(response.body)
        assert payload["error"]["code"] == "not_found"

    def test_a_malformed_body_is_a_400_not_a_crash(self, studio_app):
        response = handle(
            method="POST",
            path="/api/auth/register",
            headers={"host": "localhost", "content-type": "application/json"},
            body=b"{not json",
            app=studio_app,
        )
        assert response.status == 400
        assert json.loads(response.body)["error"]["code"] == "validation_failed"

    def test_security_headers_are_present(self, studio_app):
        response = call(studio_app, "GET", "/api/health")
        for name in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
            assert name in response.headers, name

    def test_every_response_carries_a_request_id(self, studio_app):
        response = call(studio_app, "GET", "/api/health")
        assert response.headers.get("X-Request-Id")


class TestTheMountPath:
    def test_the_prefix_is_stripped_to_theapplication_path(self):
        assert application_path("/api/studio/api/health", {}) == "/api/health"

    def test_the_platform_header_wins_when_present(self):
        headers = {"x-vercel-proxy-path": "/api/studio/api/models"}
        assert application_path("/ignored", headers) == "/api/models"

    def test_a_path_outside_the_prefix_is_left_alone(self):
        assert application_path("/api/health", {}) == "/api/health"

    def test_the_bare_prefix_maps_to_root(self):
        assert application_path("/api/studio", {}) == "/"


class TestClientAddress:
    def test_x_forwarded_for_takes_the_first_entry(self):
        assert client_ip_from({"x-forwarded-for": "203.0.113.9, 10.0.0.1"}) == "203.0.113.9"

    def test_x_real_ip_is_the_fallback(self):
        assert client_ip_from({"x-real-ip": "203.0.113.10"}) == "203.0.113.10"

    def test_no_header_is_a_stable_constant(self):
        # A random value here would let one caller past every rate limit.
        assert client_ip_from({}) == "0.0.0.0"
        assert client_ip_from({}) == client_ip_from({})


class TestTheFunctionModule:
    """The Vercel entrypoint itself, not just the package it delegates to.

    A bug shipped through every test in this file: `api/studio.py` imported
    `application_path` *inside* the request handler, which makes the name
    function-local, and the handler used it before the import ran. Every unit test
    passed because they call `studio.serverless.handle` directly and never touch
    the function module. These tests import it the way Vercel does -- by file --
    and call its handler against a real application.
    """

    @staticmethod
    def _load():
        import importlib.util

        path = os.path.join(REPO, "api", "studio.py")
        spec = importlib.util.spec_from_file_location("vf_studio_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_module_exposes_a_handler(self):
        from http.server import BaseHTTPRequestHandler

        module = self._load()
        assert issubclass(module.handler, BaseHTTPRequestHandler)

    def test_application_path_is_module_scope(self):
        """The name the handler calls before its try block must exist at import.

        If it is imported inside the function instead, `module.application_path`
        is absent and every request raises UnboundLocalError at runtime -- which
        is a 500 with a generic platform message, not a JSON error.
        """
        module = self._load()
        assert hasattr(module, "application_path"), (
            "application_path is not at module scope; the handler will raise "
            "UnboundLocalError before it can build a response"
        )
        assert hasattr(module, "handle")

    def test_a_real_request_is_served_by_the_function_module(self, studio_app):
        """Drive the actual handler with a socket pair, no server.

        `BaseHTTPRequestHandler` reads from `rfile` and writes to `wfile`, so a
        pair of in-memory buffers is enough -- and it exercises the code path
        Vercel's runtime uses, including the prefix stripping.
        """
        import io

        module = self._load()
        raw = (
            b"GET /api/studio/api/health HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        instance = module.handler.__new__(module.handler)
        instance.rfile = io.BytesIO(raw)
        instance.wfile = io.BytesIO()
        # The pieces BaseHTTPRequestHandler normally sets up per connection.
        instance.headers = {}
        instance.request_version = "HTTP/1.1"
        instance.command = "GET"
        instance.path = "/api/studio/api/health"
        instance.requestline = "GET /api/studio/api/health HTTP/1.1"
        instance.client_address = ("127.0.0.1", 0)
        instance.server = type("S", (), {"server_name": "localhost",
                                         "server_port": 80})()
        instance.handle_one_request()

        written = instance.wfile.getvalue()
        assert b"HTTP/1.1" in written, written[:200]
        assert b"200" in written.split(b"\r\n", 1)[0], written[:200]
