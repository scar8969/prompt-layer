"""Roles, permissions, and the check that runs before every authorized action.

Four roles
----------
```
viewer   read everything in the workspace, generate nothing
editor   viewer + run generations, upload, organize projects and assets
admin    editor + manage provider connections and models, invite members
owner    admin + delete the workspace, transfer ownership, change billing mode
```

The ordering is real: each role is a superset of the one below it, and
:func:`role_at_least` is a rank comparison rather than a set lookup. That keeps
the rules legible ("editing requires at least editor") instead of requiring a
matrix that has to be read as a whole.

Two decisions worth stating
---------------------------
* Permissions are derived from the membership row fetched for this request. The
  client's claim about its own role is never consulted.
* Anything that spends money -- creating a generation, testing a connection --
  requires at least `editor`. A viewer account is genuinely unable to cost the
  workspace anything, which is what makes it safe to hand out.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, List, Optional

from ..errors import FORBIDDEN, ApiError


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


#: Every role, ordered from most to least privileged. Order is meaningful.
ROLES: tuple = (
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.EDITOR,
    WorkspaceRole.VIEWER,
)

ROLE_RANK: Dict[str, int] = {
    WorkspaceRole.OWNER.value: 40,
    WorkspaceRole.ADMIN.value: 30,
    WorkspaceRole.EDITOR.value: 20,
    WorkspaceRole.VIEWER.value: 10,
}


def normalize_role(value: Optional[str]) -> str:
    """Coerce anything unrecognised to the least privileged role.

    Failing closed matters more than failing informatively here: a membership row
    with a role string we do not know is a bug or a tampering attempt, and the
    safe reading of either is "viewer".
    """
    if value in ROLE_RANK:
        return str(value)
    return WorkspaceRole.VIEWER.value


def role_at_least(role: Optional[str], minimum: WorkspaceRole) -> bool:
    return ROLE_RANK[normalize_role(role)] >= ROLE_RANK[minimum.value]


def rank(role: Optional[str]) -> int:
    return ROLE_RANK[normalize_role(role)]


class Permission(str, Enum):
    """A capability, named by the thing being done rather than the route."""

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    CONNECTION_READ = "connection:read"
    CONNECTION_MANAGE = "connection:manage"
    CONNECTION_USE = "connection:use"
    MODEL_READ = "model:read"
    MODEL_MANAGE = "model:manage"
    GENERATION_CREATE = "generation:create"
    GENERATION_READ = "generation:read"
    GENERATION_CANCEL = "generation:cancel"
    PROJECT_MANAGE = "project:manage"
    ASSET_READ = "asset:read"
    ASSET_WRITE = "asset:write"
    ASSET_DELETE = "asset:delete"
    PRESET_READ = "preset:read"
    PRESET_MANAGE = "preset:manage"
    CHARACTER_MANAGE = "character:manage"
    USAGE_READ = "usage:read"
    AUDIT_READ = "audit:read"


#: Minimum role per permission. Read it as "at least this role may do this".
_MINIMUM: Dict[Permission, WorkspaceRole] = {
    Permission.WORKSPACE_READ: WorkspaceRole.VIEWER,
    Permission.WORKSPACE_UPDATE: WorkspaceRole.ADMIN,
    Permission.WORKSPACE_DELETE: WorkspaceRole.OWNER,
    Permission.MEMBER_READ: WorkspaceRole.VIEWER,
    Permission.MEMBER_MANAGE: WorkspaceRole.ADMIN,
    # Creating, editing, rotating and deleting provider credentials is an
    # administrative act: it changes what the whole workspace can spend.
    Permission.CONNECTION_READ: WorkspaceRole.EDITOR,
    Permission.CONNECTION_MANAGE: WorkspaceRole.ADMIN,
    Permission.CONNECTION_USE: WorkspaceRole.EDITOR,
    Permission.MODEL_READ: WorkspaceRole.VIEWER,
    Permission.MODEL_MANAGE: WorkspaceRole.ADMIN,
    Permission.GENERATION_CREATE: WorkspaceRole.EDITOR,
    Permission.GENERATION_READ: WorkspaceRole.VIEWER,
    Permission.GENERATION_CANCEL: WorkspaceRole.EDITOR,
    Permission.PROJECT_MANAGE: WorkspaceRole.EDITOR,
    Permission.ASSET_READ: WorkspaceRole.VIEWER,
    Permission.ASSET_WRITE: WorkspaceRole.EDITOR,
    Permission.ASSET_DELETE: WorkspaceRole.EDITOR,
    Permission.PRESET_READ: WorkspaceRole.VIEWER,
    Permission.PRESET_MANAGE: WorkspaceRole.EDITOR,
    Permission.CHARACTER_MANAGE: WorkspaceRole.EDITOR,
    Permission.USAGE_READ: WorkspaceRole.EDITOR,
    Permission.AUDIT_READ: WorkspaceRole.ADMIN,
}


def permissions_for(role: Optional[str]) -> FrozenSet[Permission]:
    """Every permission a role holds, computed from the minimum-role table."""
    return frozenset(
        permission
        for permission, minimum in _MINIMUM.items()
        if role_at_least(role, minimum)
    )


def can(role: Optional[str], permission: Permission) -> bool:
    minimum = _MINIMUM.get(permission)
    if minimum is None:  # an unregistered permission is never granted
        return False
    return role_at_least(role, minimum)


def require(role: Optional[str], permission: Permission) -> None:
    """Raise 403 unless `role` holds `permission`.

    The message names the action, not the internal permission string, because the
    person reading it needs to know what they tried to do.
    """
    if can(role, permission):
        return
    action = _ACTION_WORDS.get(permission, "do that")
    raise ApiError(
        FORBIDDEN,
        "Your role in this workspace (%s) cannot %s." % (normalize_role(role), action),
        details={"permission": permission.value, "role": normalize_role(role)},
    )


_ACTION_WORDS: Dict[Permission, str] = {
    Permission.WORKSPACE_UPDATE: "change workspace settings",
    Permission.WORKSPACE_DELETE: "delete the workspace",
    Permission.MEMBER_MANAGE: "add or remove members",
    Permission.CONNECTION_MANAGE: "manage API connections",
    Permission.CONNECTION_USE: "use API connections",
    Permission.MODEL_MANAGE: "manage models",
    Permission.GENERATION_CREATE: "start a generation",
    Permission.GENERATION_CANCEL: "cancel a generation",
    Permission.PROJECT_MANAGE: "change projects",
    Permission.ASSET_WRITE: "add or change assets",
    Permission.ASSET_DELETE: "delete assets",
    Permission.PRESET_MANAGE: "change presets",
    Permission.CHARACTER_MANAGE: "change characters",
    Permission.USAGE_READ: "view usage",
    Permission.AUDIT_READ: "view the audit log",
}


def describe_roles() -> List[dict]:
    """The role matrix as data, for the members screen and the API docs."""
    return [
        {
            "role": role.value,
            "rank": ROLE_RANK[role.value],
            "permissions": sorted(
                permission.value for permission in permissions_for(role.value)
            ),
        }
        for role in ROLES
    ]
