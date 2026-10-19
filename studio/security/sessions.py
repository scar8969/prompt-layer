"""Sessions, CSRF, and the per-request authorization context.

Session tokens
--------------
A session is a random 32-byte URL-safe token handed to the browser in an
HttpOnly cookie. The database stores only a keyed digest of that token, so a
database dump contains nothing that can be replayed as a login. The digest is
keyed with the application secret, which means an attacker holding only the dump
cannot even run an offline dictionary attack -- there is nothing to guess, since
the token is 256 bits of randomness.

Cookies
-------
`HttpOnly` (no JavaScript access), `SameSite=Lax` (a cross-site POST does not
carry it), `Path=/`, and `Secure` whenever the deployment is served over https.
The cookie holds the session token and nothing else: no role, no workspace, no
user id. Every one of those is re-read from the database on each request, so a
stale or tampered cookie cannot assert authority.

CSRF
----
Two independent defences. `SameSite=Lax` blocks the simplest cross-site form
post. On top of that, every state-changing request must carry a CSRF token in
`X-CSRF-Token`, and the token is derived as `HMAC(auth_secret, session_id)` --
so it is bound to the one session it is valid for, cannot be replayed against a
different session, and needs no server-side storage of its own.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..crypto import (
    constant_time_equals,
    new_token,
    token_fingerprint,
)
from ..db import Database
from ..errors import FORBIDDEN, UNAUTHENTICATED, ApiError
from ..util import iso_in, is_past, now_iso
from .rbac import Permission, normalize_role, require

#: The session cookie. Named without a framework prefix so the same value works
#: whether the app is served by the dev emulator or by a platform host.
SESSION_COOKIE = "studio_session"
#: Header a state-changing request must carry. A header cannot be set by a plain
#: cross-site form post, which is exactly the point.
CSRF_HEADER = "X-CSRF-Token"
#: Readable by JavaScript so the client can echo it back in the header above.
#: It is not a secret on its own: it is worthless without the HttpOnly cookie.
CSRF_COOKIE = "studio_csrf"


@dataclass(frozen=True)
class SessionContext:
    """Everything an authorized request needs, resolved fresh from storage.

    This object is the *only* thing the route layer consults for identity. It
    carries the role as it exists in the membership table right now, not as it
    was when the session was created, so removing someone from a workspace takes
    effect on their next request rather than at logout.
    """

    session_id: str
    user_id: str
    workspace_id: str
    role: str
    email: str
    display_name: str
    workspace_name: str
    is_admin: bool = False
    expires_at: str = ""
    csrf_token: str = ""
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    # -- authorization ------------------------------------------------------
    def can(self, permission: Permission) -> bool:
        from .rbac import can as _can

        return _can(self.role, permission)

    def require(self, permission: Permission) -> None:
        require(self.role, permission)

    def require_admin(self) -> None:
        if not self.is_admin:
            raise ApiError(FORBIDDEN, "This action requires an instance administrator.")

    # -- serialization ------------------------------------------------------
    def to_public(self) -> Dict[str, Any]:
        """The session as the browser is allowed to see it. No token, ever."""
        return {
            "user": {
                "id": self.user_id,
                "email": self.email,
                "display_name": self.display_name,
                "is_admin": self.is_admin,
            },
            "workspace": {
                "id": self.workspace_id,
                "name": self.workspace_name,
                "role": normalize_role(self.role),
            },
            "expires_at": self.expires_at,
            "csrf_token": self.csrf_token,
        }


def issue_csrf_token(session_id: str, auth_secret: str) -> str:
    """Derive the CSRF token for a session.

    Derived rather than stored: it is a function of the session id and the
    application secret, so there is no second table to keep consistent, and a
    token from one session simply fails against another.
    """
    return hmac.new(
        auth_secret.encode("utf-8"),
        ("csrf:" + session_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(token: Optional[str], session_id: str, auth_secret: str) -> bool:
    if not token or not session_id:
        return False
    return constant_time_equals(token, issue_csrf_token(session_id, auth_secret))


def hash_ip(ip: str, auth_secret: str, length: int = 32) -> str:
    """A stable, non-reversible label for a client address.

    Stored instead of the address itself: enough to spot "the same client is
    hammering this", not enough to constitute a log of who connected from where.
    """
    if not ip:
        return ""
    digest = hmac.new(
        auth_secret.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:length]


class SessionService:
    """Creates, resolves and revokes sessions. One per process is enough."""

    def __init__(self, db: Database, security_config: Any) -> None:
        self._db = db
        self._config = security_config

    @property
    def _secret(self) -> str:
        return self._config.auth_secret or ""

    # -- creation -----------------------------------------------------------
    def create(
        self,
        user_id: str,
        workspace_id: str,
        *,
        user_agent: str = "",
        ip: str = "",
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[str, SessionContext]:
        """Open a session and return the plaintext token exactly once.

        The token is returned here and never stored; only the digest is written.
        This is the only moment in the system's life where the token exists in
        any form other than the client's cookie.
        """
        token = new_token(32)
        session_id = token_fingerprint(token, self._secret or "unkeyed")
        ttl = ttl_seconds or getattr(self._config, "session_ttl_seconds", 1209600)
        expires_at = iso_in(ttl)
        with self._db.write_tx() as tx:
            tx.execute(
                "INSERT INTO sessions"
                " (id, user_id, workspace_id, user_agent, ip_hash, created_at,"
                "  last_seen_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    user_id,
                    workspace_id,
                    (user_agent or "")[:400],
                    hash_ip(ip, self._secret),
                    now_iso(),
                    now_iso(),
                    expires_at,
                ),
            )
        context = self._context_for(session_id, user_id, workspace_id, expires_at)
        return token, context

    # -- resolution ---------------------------------------------------------
    def resolve(self, token: Optional[str]) -> Optional[SessionContext]:
        """Turn a cookie value into a context, or None.

        Returns None rather than raising: the caller decides whether an anonymous
        request is acceptable for the route it is serving.
        """
        if not token:
            return None
        session_id = token_fingerprint(token, self._secret or "unkeyed")
        row = self._db.query_one(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if row is None:
            return None
        if row["revoked_at"]:
            return None
        if is_past(row["expires_at"]):
            # Cleaning up lazily keeps the read path free of writes in the common
            # case while still not letting an expired row linger forever.
            self._db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now_iso(), session_id),
            )
            return None
        user = self._db.query_one(
            "SELECT id, email, display_name, is_admin, is_active FROM users WHERE id = ?",
            (row["user_id"],),
        )
        if user is None or not user["is_active"]:
            return None
        # The session records which workspace was active. Membership is
        # re-verified here, so a session cannot outlive the member's removal.
        membership = self._db.query_one(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (row["workspace_id"], row["user_id"]),
        )
        if membership is None:
            return None
        return self._context_from_rows(session_id, row["expires_at"], user, membership, row["workspace_id"])

    def touch(self, session_id: str) -> None:
        """Record activity. Called at most once per request, and cheaply."""
        self._db.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
            (now_iso(), session_id),
        )

    def _context_for(
        self, session_id: str, user_id: str, workspace_id: str, expires_at: str
    ) -> SessionContext:
        user = self._db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        membership = self._db.query_one(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        if user is None or membership is None:  # pragma: no cover - creation raced a delete
            raise ApiError(UNAUTHENTICATED, "Sign in to continue.")
        return self._context_from_rows(session_id, expires_at, user, membership, workspace_id)

    def _context_from_rows(
        self, session_id: str, expires_at: str, user: Any, membership: Any, workspace_id: str
    ) -> SessionContext:
        workspace = self._db.query_one(
            "SELECT id, name FROM workspaces WHERE id = ?", (workspace_id,)
        )
        role = normalize_role(membership["role"])
        return SessionContext(
            session_id=session_id,
            user_id=user["id"],
            workspace_id=workspace_id,
            role=role,
            email=user["email"],
            display_name=user["display_name"] or "",
            workspace_name=(workspace["name"] if workspace else ""),
            is_admin=bool(user["is_admin"]),
            expires_at=expires_at,
            csrf_token=issue_csrf_token(session_id, self._secret),
        )

    # -- mutation -----------------------------------------------------------
    def switch_workspace(self, token: str, workspace_id: str) -> SessionContext:
        """Move an existing session to another workspace the user belongs to.

        The membership is verified first: a session cannot be pointed at a
        workspace the user is not in, so this is not a privilege boundary.
        """
        context = self.resolve(token)
        if context is None:
            raise ApiError(UNAUTHENTICATED, "Sign in to continue.")
        membership = self._db.query_one(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, context.user_id),
        )
        if membership is None:
            raise ApiError(FORBIDDEN, "You are not a member of that workspace.")
        self._db.execute(
            "UPDATE sessions SET workspace_id = ?, last_seen_at = ? WHERE id = ?",
            (workspace_id, now_iso(), context.session_id),
        )
        return self._context_for(
            context.session_id, context.user_id, workspace_id, context.expires_at
        )

    def revoke(self, token: Optional[str]) -> bool:
        if not token:
            return False
        session_id = token_fingerprint(token, self._secret or "unkeyed")
        return (
            self._db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now_iso(), session_id),
            ).rowcount
            > 0
        )

    def revoke_all_for_user(self, user_id: str, *, except_session_id: Optional[str] = None) -> int:
        """Sign a user out everywhere. Used on password change, not on logout."""
        if except_session_id:
            return self._db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ?"
                " AND revoked_at IS NULL AND id <> ?",
                (now_iso(), user_id, except_session_id),
            ).rowcount
        return self._db.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now_iso(), user_id),
        ).rowcount

    def revoke_workspace_sessions(self, workspace_id: str) -> int:
        """Used when a member is removed or a workspace is deleted."""
        return self._db.execute(
            "UPDATE sessions SET revoked_at = ? WHERE workspace_id = ? AND revoked_at IS NULL",
            (now_iso(), workspace_id),
        ).rowcount

    def purge_expired(self, *, older_than_days: int = 30) -> int:
        """Housekeeping. Keeps the table from growing without bound."""
        return self._db.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (iso_in(-older_than_days * 86400),),
        ).rowcount

    # -- introspection ------------------------------------------------------
    def active_sessions(self, user_id: str) -> list:
        rows = self._db.query_dicts(
            "SELECT id, user_agent, ip_hash, created_at, last_seen_at, expires_at"
            " FROM sessions WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?"
            " ORDER BY last_seen_at DESC",
            (user_id, now_iso()),
        )
        for row in rows:
            # The digest is a stable identifier for "this browser", but it is
            # still derived from a secret, so it is truncated to a short label.
            row["id"] = row["id"][:12]
        return rows


def cookie_flags(require_https: bool) -> Dict[str, Any]:
    """The attributes every session cookie carries.

    Secure is tied to whether the deployment is actually on https. Setting it
    unconditionally would make the cookie silently undeliverable on a plain-http
    local install, which looks like "login does nothing" rather than a
    configuration problem.
    """
    return {
        "httponly": True,
        "samesite": "Lax",
        "secure": bool(require_https),
        "path": "/",
    }
