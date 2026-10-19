-- Identity and tenancy.
--
-- Every object the platform stores belongs to a workspace, and every workspace
-- has members with a role. Personal signup creates a workspace automatically, so
-- the common case has no extra step while the shared case still has somewhere to
-- put a second member.

CREATE TABLE users (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL,
    email_normalized    TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL DEFAULT '',
    password_hash       TEXT NOT NULL,
    is_admin            INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    last_login_at       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Lookup by email is case-insensitive; the normalized column carries the index
-- so the comparison is a plain equality rather than a function on every row.
CREATE INDEX idx_users_email_normalized ON users (email_normalized);

CREATE TABLE workspaces (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    owner_user_id   TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    is_personal     INTEGER NOT NULL DEFAULT 1,
    -- 'user_provider' means generation is billed by the provider the workspace
    -- connected. 'platform_credits' means the deployment provides the model and
    -- is therefore entitled to charge. The distinction is recorded rather than
    -- inferred, so no cost line can appear under the wrong party.
    billing_mode    TEXT NOT NULL DEFAULT 'user_provider'
                    CHECK (billing_mode IN ('user_provider', 'platform_credits')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_workspaces_owner ON workspaces (owner_user_id);

CREATE TABLE workspace_members (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (workspace_id, user_id)
);

CREATE INDEX idx_workspace_members_user ON workspace_members (user_id);

-- Sessions are stored by keyed digest of the cookie value. A database dump
-- therefore yields no replayable session.
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_agent      TEXT NOT NULL DEFAULT '',
    ip_hash         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked_at      TEXT
);

CREATE INDEX idx_sessions_user ON sessions (user_id);
CREATE INDEX idx_sessions_expiry ON sessions (expires_at);

-- Single-use tokens for password reset and email verification. Hashed for the
-- same reason sessions are.
CREATE TABLE auth_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL CHECK (purpose IN ('password_reset', 'email_verify')),
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_auth_tokens_user ON auth_tokens (user_id, purpose);
