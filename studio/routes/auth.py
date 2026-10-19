"""Authentication routes: register, sign in, sign out, who am I, password lifecycle."""

from __future__ import annotations

from typing import Any, Dict

from ..errors import CONFIGURATION_REQUIRED, UNAUTHENTICATED, ApiError
from ..http import RequestContext, Response, clear_cookie, cookie_header, ok
from ..logging_setup import get_logger
from ..security.sessions import CSRF_COOKIE, SESSION_COOKIE

LOG = get_logger(__name__)


def _require_configured(app: Any) -> None:
    """Refuse sign-in work when the deployment has no auth secret.

    Answering "the server is misconfigured" is the only honest answer here: a
    session issued without a signing secret is not a session.
    """
    missing = app.config.missing_for("auth")
    if missing:
        raise ApiError(
            CONFIGURATION_REQUIRED,
            "Sign-in is not configured on this deployment.",
            details={"missing": missing},
            hint="Set STUDIO_AUTH_SECRET and restart the service.",
        )


def _session_payload(app: Any, context: Any, token: str) -> Dict[str, Any]:
    """The body a sign-in returns, plus the cookie flags the caller must set."""
    return {
        "session": {
            "csrf_token": context.csrf_token,
            "expires_at": context.expires_at,
            "workspace_id": context.workspace_id,
        },
        "user": {
            "id": context.user_id,
            "email": context.email,
            "display_name": context.display_name,
            "is_admin": context.is_admin,
        },
        "workspace": {
            "id": context.workspace_id,
            "name": context.workspace_name,
            "role": context.role,
        },
    }


def _with_session_cookies(app: Any, response: Response, token: str, context: Any) -> Response:
    """Attach the session and CSRF cookies to a response.

    The session cookie is HttpOnly; the CSRF cookie is readable on purpose, so a
    page reload can recover the token without asking the server for it again.
    Both travel as one Set-Cookie header value list, which the transport writes
    as two header lines.
    """
    secure = bool(app.config.security.require_https)
    ttl = int(app.config.security.session_ttl_seconds)
    response.headers["Set-Cookie"] = [
        cookie_header(SESSION_COOKIE, token, max_age=ttl, secure=secure),
        cookie_header(CSRF_COOKIE, context.csrf_token, max_age=ttl, http_only=False, secure=secure),
    ]
    return response


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def register(request: RequestContext) -> Response:
        """Create an account and sign it in."""
        _require_configured(app)
        body = request.json()
        result = app.accounts.register(
            str(body.get("email") or ""),
            str(body.get("password") or ""),
            str(body.get("display_name") or ""),
        )
        token, context = app.sessions.create(
            result.user_id,
            result.workspace_id,
            user_agent=request.header("user-agent"),
            ip=request.client_ip,
        )
        app.repos.audit.record(
            context.workspace_id,
            context.user_id,
            "auth.registered",
            target_type="user",
            target_id=result.user_id,
            request_id=request.request_id,
        )
        response = Response.json(_session_payload(app, context, token), status=201)
        return _with_session_cookies(app, response, token, context)

    def login(request: RequestContext) -> Response:
        """Verify a credential and open a session."""
        _require_configured(app)
        body = request.json()
        record = app.accounts.authenticate(
            str(body.get("email") or ""), str(body.get("password") or "")
        )
        workspace = app.accounts.default_workspace_for(record["id"])
        if workspace is None:
            raise ApiError(
                UNAUTHENTICATED,
                "This account is not a member of any workspace.",
                hint="Ask an administrator to add you to one.",
            )
        token, context = app.sessions.create(
            record["id"],
            workspace["id"],
            user_agent=request.header("user-agent"),
            ip=request.client_ip,
        )
        app.repos.audit.record(
            context.workspace_id,
            context.user_id,
            "auth.signed_in",
            target_type="user",
            target_id=record["id"],
            request_id=request.request_id,
            ip_hash="",
        )
        response = Response.json(_session_payload(app, context, token))
        return _with_session_cookies(app, response, token, context)

    def logout(request: RequestContext) -> Response:
        """Revoke the session server-side, then clear the cookies."""
        token = request.cookie(SESSION_COOKIE)
        if token:
            app.sessions.revoke(token)
        secure = bool(app.config.security.require_https)
        response = Response.json({"ok": True})
        response.headers["Set-Cookie"] = clear_cookie(SESSION_COOKIE, secure=secure)
        return response

    def me(request: RequestContext) -> Response:
        """Who is signed in, in which workspace, and what they may do.

        The CSRF token is re-issued here so a reload recovers it from the server
        rather than depending on a readable cookie.
        """
        session = request.session
        if session is None:
            raise ApiError(UNAUTHENTICATED, "Sign in to continue.")
        from ..security.rbac import permissions_for

        payload = session.to_public()
        payload["workspaces"] = app.accounts.list_workspaces(session.user_id)
        payload["permissions"] = sorted(
            permission.value for permission in permissions_for(session.role)
        )
        return Response.json(payload)

    def change_password(request: RequestContext) -> Response:
        """Change the password of the signed-in user, then keep them signed in."""
        session = request.session
        if session is None:
            raise ApiError(UNAUTHENTICATED, "Sign in to continue.")
        body = request.json()
        app.accounts.change_password(
            session.user_id,
            str(body.get("current_password") or ""),
            str(body.get("new_password") or ""),
        )
        # Every other session is revoked: a password change is the moment to
        # assume the old one leaked.
        app.sessions.revoke_all_for_user(session.user_id, except_session_id=session.session_id)
        return Response.json({"ok": True})

    def forgot_password(request: RequestContext) -> Response:
        """Start a reset. Never reveals whether the address exists."""
        body = request.json()
        issued = app.accounts.issue_reset_token(str(body.get("email") or ""))
        payload: Dict[str, Any] = {"ok": True, "delivery": "manual"}
        if issued is not None and not app.config.is_production:
            # In development the token is returned so the flow is testable. In
            # production it would be an account takeover, so it is withheld and
            # the operator delivers it out of band.
            payload["reset_token"] = issued[0]
        return Response.json(payload)

    def reset_password(request: RequestContext) -> Response:
        """Redeem a reset token and set a new password."""
        body = request.json()
        user_id = app.accounts.redeem_reset_token(
            str(body.get("token") or ""), str(body.get("new_password") or "")
        )
        app.sessions.revoke_all_for_user(user_id)
        return Response.json({"ok": True})

    router.add("POST", "/api/auth/register", register, authenticated=False)
    router.add("POST", "/api/auth/login", login, authenticated=False)
    router.add("POST", "/api/auth/logout", logout, authenticated=False)
    router.add("POST", "/api/auth/forgot-password", forgot_password, authenticated=False)
    router.add("POST", "/api/auth/reset-password", reset_password, authenticated=False)
    router.add("GET", "/api/auth/me", me)
    router.add("POST", "/api/auth/change-password", change_password)
