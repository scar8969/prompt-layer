"""Identity, authorization, and abuse control.

Everything in this package assumes the attacker can read the database. That
assumption drives each module:

* :mod:`passwords` stores memory-hard hashes, never passwords.
* :mod:`sessions` stores keyed digests of session tokens, so a dump yields no
  replayable cookie.
* :mod:`rbac` derives permission from a membership row fetched per request,
  never from anything the client sent.
* :mod:`rate_limit` counts in the database, so limits survive a restart and are
  shared by every process rather than living in one worker's memory.
* :mod:`accounts` is the only place that creates a user, and the only place that
  decides what a new account can see.
"""

from .passwords import (
    PasswordProblem,
    describe_policy,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from .rbac import (
    ROLES,
    ROLE_RANK,
    Permission,
    WorkspaceRole,
    can,
    describe_roles,
    permissions_for,
    require,
    role_at_least,
)
from .sessions import (
    SESSION_COOKIE,
    CSRF_HEADER,
    SessionContext,
    SessionService,
    issue_csrf_token,
    verify_csrf_token,
)
from .rate_limit import RateLimitResult, RateLimiter
from .accounts import AccountService

__all__ = [
    "AccountService",
    "CSRF_HEADER",
    "PasswordProblem",
    "Permission",
    "ROLES",
    "ROLE_RANK",
    "RateLimitResult",
    "RateLimiter",
    "SESSION_COOKIE",
    "SessionContext",
    "SessionService",
    "WorkspaceRole",
    "can",
    "describe_policy",
    "describe_roles",
    "hash_password",
    "issue_csrf_token",
    "needs_rehash",
    "permissions_for",
    "require",
    "role_at_least",
    "validate_password",
    "verify_csrf_token",
    "verify_password",
]
