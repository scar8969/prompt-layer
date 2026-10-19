-- The generation queue and its outputs.
--
-- The queue is a table, not a broker, so a job survives a restart and the state
-- the user sees is the state that was actually reached. Nothing here is written
-- speculatively: a row moves to 'completed' only after an artifact has been
-- stored, and 'failed' only with a diagnostic attached.

CREATE TABLE generation_jobs (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id  TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id          TEXT REFERENCES projects (id) ON DELETE SET NULL,
    connection_id       TEXT REFERENCES api_connections (id) ON DELETE SET NULL,
    model_id            TEXT REFERENCES models (id) ON DELETE SET NULL,
    modality            TEXT NOT NULL CHECK (modality IN ('image', 'video')),
    mode                TEXT NOT NULL,
    prompt              TEXT NOT NULL DEFAULT '',
    negative_prompt     TEXT NOT NULL DEFAULT '',
    parameters_json     TEXT NOT NULL DEFAULT '{}',
    -- Snapshot of the resolved request. Kept so history is reproducible even
    -- after the model's schema is edited.
    request_json        TEXT NOT NULL DEFAULT '{}',
    references_json     TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL
                        CHECK (status IN ('draft', 'queued', 'validating', 'submitting',
                                          'processing', 'downloading', 'completed',
                                          'failed', 'cancelled', 'timed_out')),
    status_message      TEXT NOT NULL DEFAULT '',
    provider_job_ref    TEXT,
    attempt             INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    next_attempt_at     TEXT,
    -- Lease, so two workers cannot both drive the same job and double-bill it.
    lease_owner         TEXT,
    lease_expires_at    TEXT,
    cancel_requested_at TEXT,
    submitted_at        TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    -- Machine code, human message, and the provider's own words (scrubbed).
    error_code          TEXT,
    error_message       TEXT,
    error_hint          TEXT,
    provider_error_json TEXT,
    -- Estimates only, and labelled as such. Never a fabricated actual.
    cost_estimate_json  TEXT NOT NULL DEFAULT '{}',
    output_count        INTEGER NOT NULL DEFAULT 0,
    idempotency_key     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_jobs_workspace ON generation_jobs (workspace_id, created_at DESC);
CREATE INDEX idx_jobs_status ON generation_jobs (status, next_attempt_at);
CREATE INDEX idx_jobs_project ON generation_jobs (project_id, created_at DESC);
CREATE INDEX idx_jobs_user ON generation_jobs (created_by_user_id, created_at DESC);

-- Duplicate-submission protection. The ingest path claims the key before the
-- provider is contacted, so a double-click cannot buy two generations.
CREATE UNIQUE INDEX idx_jobs_idempotency ON generation_jobs (workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE generation_outputs (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    asset_id        TEXT REFERENCES assets (id) ON DELETE SET NULL,
    batch_index     INTEGER NOT NULL DEFAULT 0,
    provider_url    TEXT,
    -- Set when the provider returned a link we could not fetch. The record stays
    -- so the user can see what happened instead of an unexplained empty result.
    ingest_error    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (job_id, batch_index)
);

CREATE INDEX idx_outputs_job ON generation_outputs (job_id);
CREATE INDEX idx_outputs_asset ON generation_outputs (asset_id);

-- Append-only transition log. This is what makes "the job said processing for
-- forty minutes and then failed" answerable after the fact.
CREATE TABLE job_events (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_job_events_job ON job_events (job_id, created_at);

-- Redaction and retention sweep bookkeeping: which temporary files a job
-- created and when they were removed.
CREATE TABLE job_temp_files (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    removed_at  TEXT
);

CREATE INDEX idx_job_temp_files_job ON job_temp_files (job_id);
