-- BYOK/BYOM: the provider connections a workspace owns, and the models they
-- expose. This is the heart of the product, so the schema records capability
-- explicitly rather than inferring it from a modality label.

CREATE TABLE api_connections (
    id                       TEXT PRIMARY KEY,
    workspace_id             TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id       TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name                     TEXT NOT NULL,
    -- Which provider protocol the adapter speaks. 'mock' exists for development
    -- and is labelled as such everywhere it can appear.
    provider_slug            TEXT NOT NULL DEFAULT 'custom',
    adapter_type             TEXT NOT NULL
                             CHECK (adapter_type IN ('fal_queue', 'openai_images',
                                                     'declarative_http', 'mock')),
    base_url                 TEXT NOT NULL,
    auth_scheme              TEXT NOT NULL DEFAULT 'bearer'
                             CHECK (auth_scheme IN ('bearer', 'header', 'query', 'none')),
    auth_header_name         TEXT NOT NULL DEFAULT 'Authorization',
    -- AES-256-GCM envelope. The plaintext key exists in memory only for the
    -- duration of a request and is never written anywhere else.
    credential_ciphertext    TEXT,
    credential_key_id        TEXT,
    credential_last4         TEXT,
    credential_fingerprint   TEXT,
    -- Non-secret headers (a project id, an organization id). Secret-bearing names
    -- are rejected on write, so this column cannot become a shadow key store.
    extra_headers_json       TEXT NOT NULL DEFAULT '{}',
    -- Declarative adapter configuration: request shape, response paths, polling.
    -- Validated against a fixed schema; never executed.
    config_json              TEXT NOT NULL DEFAULT '{}',
    -- Hosts this connection is permitted to reach beyond the default egress
    -- policy. This is the auditable door for a self-hosted inference server.
    allowed_hosts_json       TEXT NOT NULL DEFAULT '[]',
    is_enabled               INTEGER NOT NULL DEFAULT 1,
    last_test_status         TEXT,
    last_test_at             TEXT,
    last_test_detail_json    TEXT,
    last_used_at             TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    deleted_at               TEXT,
    UNIQUE (workspace_id, name)
);

CREATE INDEX idx_connections_workspace ON api_connections (workspace_id, deleted_at);

CREATE TABLE models (
    id                      TEXT PRIMARY KEY,
    workspace_id            TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    connection_id           TEXT REFERENCES api_connections (id) ON DELETE CASCADE,
    -- The identifier the provider expects on the wire.
    model_key               TEXT NOT NULL,
    display_name            TEXT NOT NULL,
    provider_slug           TEXT NOT NULL DEFAULT 'custom',
    modality                TEXT NOT NULL CHECK (modality IN ('image', 'video', 'other')),
    -- Modest set of generation modes, validated on write against the modality so
    -- an image model cannot claim to extend a video.
    modes_json              TEXT NOT NULL DEFAULT '[]',
    capabilities_json       TEXT NOT NULL DEFAULT '{}',
    parameter_schema_json   TEXT NOT NULL DEFAULT '{}',
    defaults_json           TEXT NOT NULL DEFAULT '{}',
    limits_json             TEXT NOT NULL DEFAULT '{}',
    -- 'discovered' means a provider endpoint listed it; 'manual' means a human
    -- typed the identifier. Both are legitimate; the UI says which.
    source                  TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('discovered', 'manual')),
    is_enabled              INTEGER NOT NULL DEFAULT 1,
    is_favorite             INTEGER NOT NULL DEFAULT 0,
    is_default              INTEGER NOT NULL DEFAULT 0,
    last_verified_at        TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (connection_id, model_key)
);

CREATE INDEX idx_models_workspace ON models (workspace_id, modality, is_enabled);
CREATE INDEX idx_models_connection ON models (connection_id);

-- Connection test history. Kept so "last tested" is a real observation with a
-- timestamp and an outcome, not a flag someone set once.
CREATE TABLE connection_tests (
    id              TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL REFERENCES api_connections (id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    outcome         TEXT NOT NULL,
    http_status     INTEGER,
    latency_ms      INTEGER,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_connection_tests_connection ON connection_tests (connection_id, created_at DESC);
