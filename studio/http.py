"""HTTP plumbing for the studio API: routing, envelopes, cookies and limits.

This module knows nothing about generations, connections or assets. It turns a
socket into a RequestContext, hands it to a handler the route table named, and
renders whatever comes back. Everything that can be got wrong once - the error
envelope, the CSRF check, the rate-limit headers, the cookie flags - is done
here so no individual route has to remember it.

Design notes that matter:

* A route handler never writes to the socket. It returns a Response, or raises
  ApiError, and the server renders both. That is what makes the error body
  identical everywhere.
* Every request carries an id, generated here and echoed in the body and the
  X-Request-Id header, so a user can quote one string and an operator can find
  the log line.
* The request body is read with a hard cap before anything parses it.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import re
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import ApiError, INTERNAL, MALFORMED_URL, NOT_FOUND, FORBIDDEN, VALIDATION_FAILED
from .logging_setup import get_logger
from .redact import scrub_structure
from .security.sessions import CSRF_HEADER, SESSION_COOKIE
from .util import new_id, now_iso

LOG = get_logger(__name__)

#: Largest JSON body the API will parse. Uploads come in as base64, so this is
#: comfortably above a 20 MB image once encoded. A body larger than this is
#: refused before it is decoded.
MAX_BODY_BYTES = 48 * 1024 * 1024
#: Longest URL the router will consider. A longer one is a client bug or an
#: attempt to exhaust memory in the path parser.
MAX_URL_LENGTH = 8 * 1024

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class Response:
    """What a handler returns. The server knows how to write each kind."""

    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    #: A header value may be a list, which is written as one header line per
    #: item. That is how two Set-Cookie headers travel in one response.
    headers: Dict[str, Any] = field(default_factory=dict)
    is_json: bool = True

    @classmethod
    def json(cls, payload: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> "Response":
        """A JSON envelope. Serialised here so every response has one shape."""
        text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        return cls(
            status=status,
            body=text.encode("utf-8"),
            content_type="application/json; charset=utf-8",
            headers=dict(headers or {}),
            is_json=True,
        )

    @classmethod
    def bytes(
        cls,
        body: bytes,
        content_type: str = "application/octet-stream",
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
    ) -> "Response":
        """Raw bytes, for media and for the built client."""
        return cls(
            status=status,
            body=body,
            content_type=content_type,
            headers=dict(headers or {}),
            is_json=False,
        )

    @classmethod
    def redirect(cls, location: str, status: int = 303) -> "Response":
        """A redirect, used only by the SPA fallback and by logout links."""
        return cls(
            status=status,
            body=b"",
            content_type="text/plain; charset=utf-8",
            headers={"Location": location},
            is_json=False,
        )


def ok(**payload: Any) -> Response:
    """A success envelope. The caller names the resource key, as the client expects."""
    body = {"ok": True}
    body.update(payload)
    return Response.json(body)


@dataclass
class RequestContext:
    """One inbound request, decoded. Nothing here has touched the network yet."""

    method: str
    path: str
    query: Dict[str, List[str]]
    headers: Dict[str, str]
    cookies: Dict[str, str]
    body: bytes
    client_ip: str
    request_id: str
    started_at: float
    #: Filled in by the authenticate middleware, once per request.
    session: Any = None
    params: Dict[str, str] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def json(self, *, required: bool = True) -> Dict[str, Any]:
        """The request body as a JSON object.

        Refuses a non-object, invalid JSON, and an oversized declared type. An
        empty body is treated as an empty object so a POST with no arguments
        does not need a special case at every call site.
        """
        if not self.body:
            if required:
                raise ApiError(VALIDATION_FAILED, "This request needs a JSON body.")
            return {}
        try:
            payload = json.loads(self.body.decode("utf-8", errors="replace"))
        except ValueError as error:
            raise ApiError(
                VALIDATION_FAILED,
                "The request body is not valid JSON.",
                provider_message=str(error),
            ) from None
        if not isinstance(payload, dict):
            raise ApiError(VALIDATION_FAILED, "The request body must be a JSON object.")
        return payload

    def arg(self, name: str, default: str = "") -> str:
        """First value of a query parameter, or the default."""
        values = self.query.get(name) or []
        return values[0] if values else default

    def flag(self, name: str) -> Optional[bool]:
        """A query parameter read as a tri-state boolean."""
        raw = self.arg(name).strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return None

    def header(self, name: str, default: str = "") -> str:
        """A header, case-insensitively."""
        return self.headers.get(name.lower(), default)

    def cookie(self, name: str) -> str:
        return self.cookies.get(name, "")

    @property
    def csrf_header(self) -> str:
        return self.header(CSRF_HEADER)

    @property
    def is_safe_method(self) -> bool:
        return self.method in _SAFE_METHODS


Handler = Callable[[RequestContext], Response]


@dataclass(frozen=True)
class Route:
    """One entry in the route table."""

    method: str
    pattern: "re.Pattern[str]"
    handler: Handler
    name: str
    #: The template this route was declared with, e.g. /api/jobs/{id}/events.
    #: Kept because the compiled pattern cannot be read backwards reliably, and
    #: the contract test needs to compare declared routes against what the client
    #: actually calls.
    template: str = ""
    #: True when the route needs a session. Almost everything does.
    authenticated: bool = True
    #: True when the route only an instance administrator may call.
    admin_only: bool = False


class Router:
    """A small path-pattern router. Explicit, inspectable, and easy to list.

    Patterns use {name} segments matched against a single path component, so a
    route cannot accidentally swallow slashes. Nothing here does wildcard
    matching or regex supplied by a caller.
    """

    def __init__(self) -> None:
        self._routes: List[Route] = []

    def add(
        self,
        method: str,
        path: str,
        handler: Handler,
        *,
        name: str = "",
        authenticated: bool = True,
        admin_only: bool = False,
    ) -> None:
        """Register a handler for one method and path template."""
        pattern = _compile_path(path)
        self._routes.append(
            Route(
                method=method.upper(),
                pattern=pattern,
                handler=handler,
                name=name or handler.__name__,
                template=path,
                authenticated=authenticated,
                admin_only=admin_only,
            )
        )

    def __len__(self) -> int:
        """How many routes are registered. A count nobody can fake."""
        return len(self._routes)

    def get(self, path: str, **kwargs: Any) -> Callable[[Handler], Handler]:
        """Decorator form: @router.get("/api/jobs/{id}")."""

        def decorate(handler: Handler) -> Handler:
            self.add("GET", path, handler, **kwargs)
            return handler

        return decorate

    def post(self, path: str, **kwargs: Any) -> Callable[[Handler], Handler]:
        def decorate(handler: Handler) -> Handler:
            self.add("POST", path, handler, **kwargs)
            return handler

        return decorate

    def patch(self, path: str, **kwargs: Any) -> Callable[[Handler], Handler]:
        def decorate(handler: Handler) -> Handler:
            self.add("PATCH", path, handler, **kwargs)
            return handler

        return decorate

    def delete(self, path: str, **kwargs: Any) -> Callable[[Handler], Handler]:
        def decorate(handler: Handler) -> Handler:
            self.add("DELETE", path, handler, **kwargs)
            return handler

        return decorate

    def resolve(self, method: str, path: str) -> Tuple[Optional[Route], Dict[str, str]]:
        """Find the route for this method and path, with its captured parameters.

        A path that exists under a different method is reported separately so the
        caller can answer 405 rather than 404, which is the difference between
        "you are at the wrong address" and "you used the wrong verb".
        """
        allowed_elsewhere = False
        for route in self._routes:
            match = route.pattern.match(path)
            if not match:
                continue
            if route.method != method.upper():
                allowed_elsewhere = True
                continue
            return route, {key: unquote(value) for key, value in match.groupdict().items()}
        if allowed_elsewhere:
            raise ApiError(
                MALFORMED_URL,
                "That address does not accept %s requests." % method.upper(),
                status=405,
            )
        return None, {}

    def describe(self) -> List[Dict[str, Any]]:
        """The route table as data: method, template, regex, name, auth flags.

        The template is what the route was declared with, and it is what a
        contract test compares the client against. The compiled regex is kept
        beside it because it cannot be read backwards reliably.
        """
        return [
            {
                "method": route.method,
                "path": route.template,
                "pattern": route.pattern.pattern.replace("^", "").replace("$", ""),
                "name": route.name,
                "authenticated": route.authenticated,
                "admin_only": route.admin_only,
            }
            for route in self._routes
        ]

    def __len__(self) -> int:
        return len(self._routes)


def _compile_path(path: str) -> "re.Pattern[str]":
    """Turn /api/jobs/{id}/events into an anchored pattern. No nesting, no regex."""
    parts = [part for part in path.split("/") if part]
    out = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1].strip()
            if not name.isidentifier():
                raise ValueError("Route parameter %r is not a valid name" % part)
            out.append("(?P<%s>[^/]+)" % name)
        else:
            out.append(re.escape(part))
    return re.compile("^/" + "/".join(out) + "$")


def parse_cookies(header: str) -> Dict[str, str]:
    """Cookie header to a dict. Never raises on malformed input."""
    jar: Dict[str, str] = {}
    if not header:
        return jar
    parsed = SimpleCookie()
    try:
        parsed.load(header)
    except Exception:
        return jar
    for key, morsel in parsed.items():
        jar[key] = morsel.value
    return jar


def cookie_header(
    name: str,
    value: str,
    *,
    max_age: Optional[int] = None,
    http_only: bool = True,
    secure: bool = False,
    same_site: str = "Lax",
    path: str = "/",
) -> str:
    """Build a Set-Cookie value. The flags are the security policy, in one place."""
    parts = ["%s=%s" % (name, value), "Path=%s" % path, "SameSite=%s" % same_site]
    if max_age is not None:
        parts.append("Max-Age=%d" % max_age)
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(name: str, *, secure: bool = False, path: str = "/") -> str:
    """Expire a cookie immediately."""
    return cookie_header(name, "", max_age=0, secure=secure, path=path)
@dataclass
class HttpLimits:
    """What the transport enforces, taken from configuration at startup."""

    max_body_bytes: int = MAX_BODY_BYTES
    rate_limit_per_minute: int = 600
    credential_rate_limit_per_minute: int = 30
    require_https: bool = False
    session_ttl_seconds: int = 60 * 60 * 24 * 14


class HttpApp:
    """Binds a router, a session service, a rate limiter and an origin check.

    The app is constructed once at startup and shared by every request thread.
    It holds no per-request state: everything a handler needs arrives in the
    RequestContext, which is created per request and then thrown away.
    """

    def __init__(
        self,
        router: Router,
        *,
        sessions: Any,
        limiter: Any,
        config: Any,
        database: Any = None,
        hooks: Optional[Dict[str, Callable[[RequestContext], None]]] = None,
    ) -> None:
        self.router = router
        self.sessions = sessions
        self.limiter = limiter
        self.config = config
        #: Held so a finished request can hand its Postgres connection back to
        #: the pool. Without it, one thread per request leaks one connection per
        #: request until the server refuses new ones.
        self.database = database
        self.hooks = dict(hooks or {})
        self.limits = HttpLimits(
            max_body_bytes=MAX_BODY_BYTES,
            rate_limit_per_minute=config.security.rate_limit_per_minute,
            credential_rate_limit_per_minute=config.security.credential_rate_limit_per_minute,
            require_https=config.security.require_https,
            session_ttl_seconds=config.security.session_ttl_seconds,
        )

    # -- request lifecycle ---------------------------------------------------

    def handle(self, request: RequestContext) -> Response:
        """Run one request end to end. Always returns a Response, never raises."""
        try:
            response = self._dispatch(request)
        except ApiError as error:
            response = self.error_response(request, error)
        except Exception as error:  # noqa: BLE001 - the last line of defence
            LOG.exception(
                "unhandled error in %s %s",
                request.method,
                request.path,
                extra={"request_id": request.request_id},
            )
            internal = ApiError(
                INTERNAL,
                "The server hit an unexpected condition while handling this request.",
                provider_message=type(error).__name__,
            )
            response = self.error_response(request, internal)
        finally:
            # One HTTP thread per request, so without this every request would
            # hold its Postgres connection until the pool timed it out.
            if self.database is not None:
                with contextlib.suppress(Exception):
                    self.database.release()
        return self._decorate(request, response)

    def _dispatch(self, request: RequestContext) -> Response:
        """Origin check, rate limit, authentication, then the handler."""
        self._check_origin(request)
        self._rate_limit(request)
        route, params = self.router.resolve(request.method, request.path)
        if route is None:
            raise ApiError(NOT_FOUND, "There is no route at that address.", status=404)
        request.params = params
        if route.authenticated:
            self._authenticate(request, route)
        if route.admin_only and not (request.session is not None and request.session.is_admin):
            raise ApiError(FORBIDDEN, "This action requires an instance administrator.")
        hook = self.hooks.get(route.name)
        if hook is not None:
            hook(request)
        return route.handler(request)

    def _check_origin(self, request: RequestContext) -> None:
        """Refuse a state-changing request that did not come from our own page.

        Two independent checks, because either alone is bypassable: an Origin or
        Referer that is not this deployment, and a missing or wrong CSRF header
        compared against the token derived from the session.
        """
        if request.is_safe_method:
            return
        origin = request.header("origin")
        referer = request.header("referer")
        allowed = self._allowed_origins()
        if origin:
            if origin.rstrip("/") not in allowed:
                raise ApiError(FORBIDDEN, "This request came from a different site.")
        elif referer:
            base = urlsplit(referer)
            candidate = "%s://%s" % (base.scheme, base.netloc)
            if candidate not in allowed:
                raise ApiError(FORBIDDEN, "This request came from a different site.")
        # A request with no session cannot be a cross-site request forgery: there
        # is no victim session to ride. Sign-in and registration therefore rely
        # on the origin check above, and the CSRF header is required only once a
        # session cookie is actually present.
        has_session = bool(request.cookie(SESSION_COOKIE))
        supplied = request.csrf_header
        if not supplied:
            if has_session:
                raise ApiError(
                    FORBIDDEN,
                    "This request is missing its CSRF token.",
                    hint="Reload the page, then try again. The token is issued when you sign in.",
                )
            return
        token = request.cookie("studio_csrf")
        if token and token != supplied:
            raise ApiError(
                FORBIDDEN,
                "The CSRF token in this request does not match the one issued to this session.",
                hint="Reload the page, then try again.",
            )

    def _allowed_origins(self) -> List[str]:
        """The origins that count as this deployment."""
        origins = []
        for url in (self.config.public_app_url, self.config.api_base_url):
            text = (url or "").strip().rstrip("/")
            if text:
                parts = urlsplit(text)
                if parts.scheme and parts.netloc:
                    origins.append("%s://%s" % (parts.scheme, parts.netloc))
        # The dev emulator and the Vite dev server both talk to this from
        # localhost, which is not necessarily the configured public URL.
        for extra in ("http://127.0.0.1:19387", "http://localhost:19387"):
            origins.append(extra)
        for port in (8090, 8091, 5173, 4173):
            origins.append("http://127.0.0.1:%d" % port)
            origins.append("http://localhost:%d" % port)
        return list(dict.fromkeys(origins))

    def _rate_limit(self, request: RequestContext) -> None:
        """Count this request against the client bucket and set the headers."""
        if self.limiter is None:
            return
        credential_touching = "/connections" in request.path or "/auth/" in request.path
        limit = (
            self.limits.credential_rate_limit_per_minute
            if credential_touching
            else self.limits.rate_limit_per_minute
        )
        result = self.limiter.check(
            "ip:%s" % request.client_ip,
            limit=limit,
            window_seconds=60,
        )
        request.context["rate_limit"] = result
        if not result.allowed:
            raise ApiError(
                "rate_limit_exceeded",
                "Too many requests arrived from this address in a short window.",
                details={"limit": limit, "retry_after_seconds": result.retry_after_seconds},
                hint="Wait a moment, then retry.",
            )

    def _authenticate(self, request: RequestContext, route: Route) -> None:
        """Resolve the session cookie, then verify the CSRF token against it."""
        token = request.cookie(SESSION_COOKIE)
        session = self.sessions.resolve(token) if token else None
        if session is None:
            raise ApiError(
                "unauthenticated",
                "Sign in to continue.",
                hint="Your session may have expired.",
            )
        request.session = session
        if not token:
            return
        supplied = request.csrf_header
        if request.is_safe_method:
            return
        if not supplied:
            raise ApiError(FORBIDDEN, "This request is missing its CSRF token.")
        expected = session.csrf_token
        if expected and supplied != expected:
            raise ApiError(
                FORBIDDEN,
                "The CSRF token in this request does not match this session.",
                hint="Reload the page, then try again.",
            )

    # -- responses ------------------------------------------------------------

    def error_response(self, request: RequestContext, error: ApiError) -> Response:
        """Render an ApiError. The provider message is scrubbed, never raw."""
        payload = error.to_payload(request.request_id)
        if request.session is not None:
            payload["session"] = {"workspace_id": getattr(request.session, "workspace_id", "")}
        safe = scrub_structure(payload)
        return Response.json(safe, status=error.status)

    def _decorate(self, request: RequestContext, response: Response) -> Response:
        """Add the headers every response carries. Idempotent and order-free."""
        headers = response.headers
        headers.setdefault("X-Request-Id", request.request_id)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Cache-Control", "no-store")
        limit = request.context.get("rate_limit")
        if limit is not None:
            headers.setdefault("X-RateLimit-Limit", str(limit.limit))
            headers.setdefault("X-RateLimit-Remaining", str(max(0, limit.remaining)))
        if self.limits.require_https:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        response.headers = headers
        return response


def make_request(
    *,
    method: str,
    raw_path: str,
    headers: Dict[str, str],
    body: bytes,
    client_ip: str,
    max_body_bytes: int = MAX_BODY_BYTES,
) -> RequestContext:
    """Decode a raw request into a context, refusing what it cannot represent.

    Refuses a URL longer than MAX_URL_LENGTH and a body over the caller limit.
    Both are refused before any parsing happens.
    """
    if len(raw_path) > MAX_URL_LENGTH:
        raise ApiError(MALFORMED_URL, "That address is too long for this server.")
    if len(body) > max_body_bytes:
        raise ApiError(
            VALIDATION_FAILED,
            "The request body is larger than this deployment accepts.",
            details={"bytes": len(body), "limit": max_body_bytes},
        )
    split = urlsplit(raw_path)
    path = unquote(split.path or "/")
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = {key: values for key, values in parse_qs(split.query, keep_blank_values=True).items()}
    lowered = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    cookies = parse_cookies(lowered.get("cookie", ""))
    return RequestContext(
        method=method.upper(),
        path=path,
        query=query,
        headers=lowered,
        cookies=cookies,
        body=body,
        client_ip=client_ip,
        request_id=new_id(),
        started_at=time.monotonic(),
    )


class StudioRequestHandler(BaseHTTPRequestHandler):
    """The socket-level handler. It reads a request and delegates everything."""

    server_version = "StudioPlatform/1.0"
    sys_version = ""
    #: Set by the server factory.
    app: HttpApp = None  # type: ignore[assignment]
    #: Root directory of the built client, when one is present.
    static_root: str = ""

    # -- HTTP verbs ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self._serve("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._serve("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        self._serve("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._serve("OPTIONS")

    # -- plumbing ------------------------------------------------------------

    def _serve(self, method: str) -> None:
        """Read, dispatch, write. An error here still produces a JSON body."""
        started = time.monotonic()
        try:
            body = self._read_body()
            request = make_request(
                method=method,
                raw_path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
                client_ip=self._client_ip(),
                max_body_bytes=self.app.limits.max_body_bytes,
            )
            if method == "OPTIONS":
                response = Response.json({"ok": True})
                request.context["rate_limit"] = None
            elif request.path.startswith("/api/"):
                response = self.app.handle(request)
            else:
                response = self._static(request)
        except ApiError as error:
            response = Response.json(
                scrub_structure(error.to_payload()),
                status=error.status,
            )
        except Exception as error:  # noqa: BLE001
            LOG.exception("transport failure on %s %s", method, self.path)
            response = Response.json(
                {
                    "error": {
                        "code": INTERNAL,
                        "message": "The server could not read this request.",
                        "provider_message": type(error).__name__,
                    }
                },
                status=500,
            )
        self._write(method, response, started)

    def _read_body(self) -> bytes:
        """Read the body, bounded, refusing a declared length over the cap."""
        declared = self.headers.get("Content-Length")
        limit = self.app.limits.max_body_bytes
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                raise ApiError(VALIDATION_FAILED, "The Content-Length header is not a number.") from None
            if length < 0:
                raise ApiError(VALIDATION_FAILED, "The Content-Length header is negative.")
            if length > limit:
                raise ApiError(
                    VALIDATION_FAILED,
                    "The request body is larger than this deployment accepts.",
                    details={"declared": length, "limit": limit},
                )
            return self.rfile.read(length) if length else b""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            raise ApiError(
                VALIDATION_FAILED,
                "This server does not accept chunked request bodies.",
                hint="Send a Content-Length header instead.",
            )
        return b""

    def _client_ip(self) -> str:
        """The caller address, honouring X-Forwarded-For only when configured."""
        trust = getattr(self.app.config.security, "trust_proxy_headers", False)
        if trust:
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0] if self.client_address else "0.0.0.0"

    def _static(self, request: RequestContext) -> Response:
        """Serve the built client, or say clearly that it has not been built."""
        root = os.path.abspath(self.static_root or "")
        if not root or not os.path.isdir(root):
            raise ApiError(
                NOT_FOUND,
                "The application is running, but no built client was found to serve.",
                details={"looked_for": root or "(unset)"},
                hint="Build the client, then point the server at its output directory.",
            )
        relative = request.path.lstrip("/") or "index.html"
        candidate = os.path.abspath(os.path.join(root, relative))
        if not candidate.startswith(root + os.sep) and candidate != root:
            raise ApiError(FORBIDDEN, "That file is outside the served directory.")
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "index.html")
        if not os.path.isfile(candidate):
            # Client-side routing: an unknown path is a page, not a 404, as long
            # as the request was for a document rather than an asset.
            if "." in os.path.basename(relative):
                raise ApiError(NOT_FOUND, "That file does not exist.", status=404)
            candidate = os.path.join(root, "index.html")
            if not os.path.isfile(candidate):
                raise ApiError(NOT_FOUND, "That file does not exist.", status=404)
        with open(candidate, "rb") as handle:
            payload = handle.read()
        content_type = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        return Response.bytes(payload, content_type=content_type)

    def _write(self, method: str, response: Response, started: float) -> None:
        """Write the response. A client that vanished mid-write is not an error."""
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for name, value in response.headers.items():
                if isinstance(value, (list, tuple)):
                    # Repeated header, which is how two Set-Cookie values travel.
                    for item in value:
                        self.send_header(name, str(item))
                else:
                    self.send_header(name, str(value))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            return
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOG.info(
            "%s %s %d %dms",
            method,
            self.path.split("?")[0],
            response.status,
            elapsed_ms,
            extra={"request_id": response.headers.get("X-Request-Id", "")},
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence the default stderr access log; the structured log above is enough."""
        return


def build_server(
    app: HttpApp,
    *,
    host: str = "127.0.0.1",
    port: int = 8091,
    static_root: str = "",
    daemon_threads: bool = True,
) -> ThreadingHTTPServer:
    """Create a threaded server bound to an address. Does not start serving."""
    handler = type(
        "BoundStudioHandler",
        (StudioRequestHandler,),
        {"app": app, "static_root": static_root},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = daemon_threads
    server.allow_reuse_address = True
    return server
