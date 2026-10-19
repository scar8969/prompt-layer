-- Operations: what was used, what was done, and the rate-limit counters.

CREATE TABLE usage_records (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id             TEXT REFERENCES users (id) ON DELETE SET NULL,
    job_id              TEXT REFERENCES generation_jobs (id) ON DELETE SET NULL,
    connection_id       TEXT REFERENCES api_connections (id) ON DELETE SET NULL,
    model_id            TEXT REFERENCES models (id) ON DELETE SET NULL,
    -- Who is paying. Recorded per row so a report can never mix the two.
    billing_party       TEXT NOT NULL CHECK (billing_party IN ('user_provider', 'platform')),
    modality            TEXT NOT NULL DEFAULT 'image',
    -- Units the platform actually observed (outputs, bytes, seconds) and, when a
    -- provider reported a price, what it reported. An absent actual stays null:
    -- inventing one would be a fabricated invoice.
    units_json          TEXT NOT NULL DEFAULT '{}',
    estimated_cost_json TEXT NOT NULL DEFAULT '{}',
    actual_cost_json    TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX idx_usage_workspace ON usage_records (workspace_id, created_at DESC);
CREATE INDEX idx_usage_job ON usage_records (job_id);

CREATE TABLE audit_log (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces (id) ON DELETE SET NULL,
    user_id         TEXT REFERENCES users (id) ON DELETE SET NULL,
    action          TEXT NOT NULL,
    target_type     TEXT NOT NULL DEFAULT '',
    target_id       TEXT NOT NULL DEFAULT '',
    request_id      TEXT NOT NULL DEFAULT '',
    ip_hash         TEXT NOT NULL DEFAULT '',
    detail_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_audit_workspace ON audit_log (workspace_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_log (action, created_at DESC);

-- Fixed-window counters. A row per (bucket, window) is cheap, survives restarts,
-- and is trivially inspectable when someone asks why they were throttled.
CREATE TABLE rate_limits (
    bucket          TEXT NOT NULL,
    window_start    INTEGER NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, window_start)
);

-- Idempotency for non-generation writes as well, so a retried POST from a flaky
-- client is not a second row.
CREATE TABLE idempotency_keys (
    key             TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id         TEXT REFERENCES users (id) ON DELETE SET NULL,
    scope           TEXT NOT NULL,
    request_hash    TEXT NOT NULL DEFAULT '',
    response_json   TEXT,
    status_code     INTEGER,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE INDEX idx_idempotency_expiry ON idempotency_keys (expires_at);

-- Worker heartbeats, so /health can say whether generation is actually being
-- processed rather than merely configured.
CREATE TABLE worker_heartbeats (
    id              TEXT PRIMARY KEY,
    worker_id       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    current_job_id  TEXT,
    started_at      TEXT NOT NULL,
    heartbeat_at    TEXT NOT NULL
);

CREATE INDEX idx_worker_heartbeats_worker ON worker_heartbeats (worker_id);
