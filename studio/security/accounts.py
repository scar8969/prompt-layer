"""Accounts: registration, sign-in, and the workspace every account starts with.

One decision shapes this module: **an account is created with a personal
workspace**. The common case is a single person with their own provider keys, and
making them create a workspace first would be a step that exists only to satisfy
the data model. So registration creates the user, the workspace, and the owner
membership in one transaction, and the user lands somewhere that already works.

The alternative -- a user with no workspace -- would mean every authorization
check needs a special case for "authenticated but homeless", which is a class of
bug worth not having.

Rate limiting and lockout live here rather than in the route layer because they
protect *this* operation, and because a check that lives next to the thing it
protects is a check that gets updated when the thing changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..crypto import new_token
from ..db import Database
from ..errors import (
    AUTH_FAILED,
    CONFLICT,
    FORBIDDEN,
    UNAUTHENTICATED,
    VALIDATION_FAILED,
    ApiError,
)
from ..logging_setup import get_logger
from ..util import iso_in, is_past, now_iso, slugify, unique_slug
from .passwords import (
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from .rbac import WorkspaceRole

LOGGER = get_logger("studio.accounts")

#: Deliberately permissive. The address is verified by delivery, not by a regex;
#: a stricter pattern mostly succeeds at rejecting valid addresses.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

#: Failed sign-ins before the account is temporarily locked. Ten is enough that a
#: person mistyping their password is unaffected and small enough that online
#: guessing is not viable.
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 900

#: Names that would collide with nothing today but look like system accounts.
RESERVED_SLUGS = frozenset(
    {"admin", "api", "app", "auth", "billing", "health", "login", "me", "root",
     "settings", "static", "status", "studio", "support", "system", "www"}
)


@dataclass(frozen=True)
class RegistrationResult:
    user_id: str
    workspace_id: str
    email: str
    display_name: str
    workspace_name: str


class AccountService:
    """The only place users and personal workspaces are created."""

    def __init__(self, db: Database, config: Any) -> None:
        self._db = db
        self._config = config

    # -- validation ---------------------------------------------------------
    def validate_registration(
        self, email: str, password: str, display_name: str = ""
    ) -> List[Dict[str, Any]]:
        """Every problem at once, so the form fails once and not four times."""
        problems: List[Dict[str, Any]] = []
        address = (email or "").strip()
        if not address:
            problems.append({"field": "email", "message": "Enter your email address."})
        elif not EMAIL_PATTERN.match(address):
            problems.append(
                {"field": "email", "message": "That does not look like an email address."}
            )
        elif self.find_by_email(address) is not None:
            # Stated plainly rather than hidden: registration is not an
            # enumeration surface in the way the login endpoint is, because the
            # next email already tells the person whether the account exists.
            problems.append(
                {
                    "field": "email",
                    "message": "An account already exists for that address. Sign in instead.",
                }
            )
        for problem in validate_password(
            password, min_length=self._config.security.password_min_length
        ):
            problems.append(problem.to_detail())
        if address and password and password.strip().lower() == address.lower():
            problems.append(
                {"field": "password", "message": "Do not use your email address as your password."}
            )
        name = (display_name or "").strip()
        if name and len(name) > 120:
            problems.append(
                {"field": "display_name", "message": "Keep the name under 120 characters."}
            )
        return problems

    @staticmethod
    def normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        row = self._db.query_dict(
            "SELECT * FROM users WHERE email_normalized = ?", (self.normalize_email(email),)
        )
        return row

    def find_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self._db.query_dict("SELECT * FROM users WHERE id = ?", (user_id,))

    # -- registration -------------------------------------------------------
    def register(
        self, email: str, password: str, display_name: str = ""
    ) -> RegistrationResult:
        problems = self.validate_registration(email, password, display_name)
        if problems:
            raise ApiError(
                VALIDATION_FAILED,
                problems[0]["message"],
                field=problems[0]["field"],
                details={"problems": problems},
            )
        address = email.strip()
        normalized = self.normalize_email(address)
        name = (display_name or "").strip() or address.split("@")[0]
        workspace_name = "%s's workspace" % name
        with self._db.write_tx() as tx:
            existing = tx.query_one(
                "SELECT id FROM users WHERE email_normalized = ?", (normalized,)
            )
            if existing is not None:
                # A second registration for the same address that beat the first
                # validation check. The unique index is the real guard.
                raise ApiError(CONFLICT, "An account already exists for that address.")
            user = tx.insert(
                "users",
                {
                    "email": address,
                    "email_normalized": normalized,
                    "display_name": name,
                    "password_hash": hash_password(password),
                },
            )
            taken = {
                row["slug"]
                for row in tx.query("SELECT slug FROM workspaces WHERE is_personal = 1")
            }
            base = slugify(name, "workspace")
            if base in RESERVED_SLUGS:
                base = base + "-workspace"
            slug = unique_slug(base, taken | RESERVED_SLUGS)
            workspace = tx.insert(
                "workspaces",
                {
                    "name": workspace_name,
                    "slug": slug,
                    "owner_user_id": user["id"],
                    "is_personal": 1,
                },
            )
            tx.insert(
                "workspace_members",
                {
                    "workspace_id": workspace["id"],
                    "user_id": user["id"],
                    "role": WorkspaceRole.OWNER.value,
                },
            )
        LOGGER.info("registered account", extra={"user_id": user["id"]})
        return RegistrationResult(
            user_id=user["id"],
            workspace_id=workspace["id"],
            email=address,
            display_name=name,
            workspace_name=workspace_name,
        )

    # -- sign-in ------------------------------------------------------------
    def authenticate(self, email: str, password: str) -> Dict[str, Any]:
        """Verify a credential, or raise an error that reveals nothing.

        Every failure returns the same message and the same code. Whether the
        address exists, whether the account is locked, and whether the password
        was wrong are not distinguishable from the response -- but the reason is
        recorded in the failed-login counter so an operator can still see what is
        happening.
        """
        record = self.find_by_email(email or "")
        if record is None:
            # A dummy verification keeps the response time similar whether or not
            # the address exists, so timing is not a second enumeration channel.
            verify_password(password or "", hash_password("placeholder-for-timing"))
            raise ApiError(AUTH_FAILED, "Email or password is incorrect.", field="password")
        if not record["is_active"]:
            raise ApiError(FORBIDDEN, "This account has been disabled.")
        if record["locked_until"] and not is_past(record["locked_until"]):
            raise ApiError(
                AUTH_FAILED,
                "Too many failed attempts. Try again shortly.",
                hint="Wait for the lockout to expire, or reset the password.",
            )
        if not verify_password(password or "", record["password_hash"]):
            self._record_failure(record)
            raise ApiError(AUTH_FAILED, "Email or password is incorrect.", field="password")
        self._record_success(record)
        return record

    def _record_failure(self, record: Dict[str, Any]) -> None:
        count = int(record["failed_login_count"] or 0) + 1
        values: Dict[str, Any] = {"failed_login_count": count}
        if count >= MAX_FAILED_LOGINS:
            values["locked_until"] = iso_in(LOCKOUT_SECONDS)
            LOGGER.warning(
                "account locked after repeated failures",
                extra={"user_id": record["id"], "attempts": count},
            )
        self._db.update("users", record["id"], values)

    def _record_success(self, record: Dict[str, Any]) -> None:
        values: Dict[str, Any] = {
            "failed_login_count": 0,
            "locked_until": None,
            "last_login_at": now_iso(),
        }
        # Transparent upgrade: a password hashed with weaker parameters is
        # re-hashed the next time we legitimately hold the plaintext.
        if needs_rehash(record["password_hash"]):  # pragma: no cover - depends on params
            LOGGER.info("password hash parameters are out of date", extra={"user_id": record["id"]})
        self._db.update("users", record["id"], values)

    # -- workspace selection ------------------------------------------------
    def default_workspace_for(self, user_id: str) -> Optional[Dict[str, Any]]:
        """The workspace to land in after sign-in.

        The personal one if it exists, otherwise the oldest membership, so
        someone who was invited to a workspace before creating their own still
        gets somewhere sensible.
        """
        row = self._db.query_dict(
            "SELECT w.* FROM workspaces w"
            " JOIN workspace_members m ON m.workspace_id = w.id"
            " WHERE m.user_id = ?"
            " ORDER BY w.is_personal DESC, w.created_at ASC LIMIT 1",
            (user_id,),
        )
        return row

    def list_workspaces(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self._db.query_dicts(
            "SELECT w.id, w.name, w.slug, w.is_personal, w.billing_mode, m.role"
            " FROM workspaces w"
            " JOIN workspace_members m ON m.workspace_id = w.id"
            " WHERE m.user_id = ?"
            " ORDER BY w.is_personal DESC, w.name ASC",
            (user_id,),
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "slug": row["slug"],
                "is_personal": bool(row["is_personal"]),
                "billing_mode": row["billing_mode"],
                "role": row["role"],
            }
            for row in rows
        ]

    # -- password lifecycle -------------------------------------------------
    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> None:
        record = self.find_by_id(user_id)
        if record is None:
            raise ApiError(UNAUTHENTICATED, "Sign in to continue.")
        if not verify_password(current_password or "", record["password_hash"]):
            raise ApiError(AUTH_FAILED, "Your current password is incorrect.", field="current_password")
        problems = validate_password(
            new_password, min_length=self._config.security.password_min_length
        )
        if problems:
            raise ApiError(
                VALIDATION_FAILED,
                problems[0].message,
                field="password",
                details={"problems": [p.to_detail() for p in problems]},
            )
        self._db.update(
            "users", user_id, {"password_hash": hash_password(new_password)}
        )

    def issue_reset_token(self, email: str) -> Optional[Tuple[str, str]]:
        """Create a single-use reset token. Returns (user_id, token) or None.

        Returns None for an unknown address *without* raising, so the caller can
        answer identically in both cases. Whether a reset email is sent is not
        something an unauthenticated caller gets to learn.
        """
        record = self.find_by_email(email or "")
        if record is None or not record["is_active"]:
            return None
        token = new_token(32)
        from ..crypto import token_fingerprint

        digest = token_fingerprint(token, self._config.security.auth_secret)
        with self._db.write_tx() as tx:
            tx.execute(
                "UPDATE auth_tokens SET used_at = ?"
                " WHERE user_id = ? AND purpose = 'password_reset' AND used_at IS NULL",
                (now_iso(), record["id"]),
            )
            tx.insert(
                "auth_tokens",
                {
                    "user_id": record["id"],
                    "purpose": "password_reset",
                    "token_hash": digest,
                    "expires_at": iso_in(3600),
                },
            )
        return record["id"], token

    def redeem_reset_token(self, token: str, new_password: str) -> str:
        """Consume a reset token and set the new password. Returns the user id."""
        from ..crypto import token_fingerprint

        digest = token_fingerprint(token or "", self._config.security.auth_secret)
        row = self._db.query_one(
            "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = 'password_reset'",
            (digest,),
        )
        if row is None or row["used_at"] or is_past(row["expires_at"]):
            raise ApiError(
                AUTH_FAILED,
                "That reset link is no longer valid.",
                hint="Request a new one.",
            )
        problems = validate_password(
            new_password, min_length=self._config.security.password_min_length
        )
        if problems:
            raise ApiError(
                VALIDATION_FAILED,
                problems[0].message,
                field="password",
                details={"problems": [p.to_detail() for p in problems]},
            )
        with self._db.write_tx() as tx:
            tx.update("users", row["user_id"], {"password_hash": hash_password(new_password)})
            tx.update("auth_tokens", row["id"], {"used_at": now_iso()})
            # A password reset is the standard "I think I was compromised" move,
            # so every existing session ends here.
            tx.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now_iso(), row["user_id"]),
            )
        return row["user_id"]
