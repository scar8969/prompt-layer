-- Saved canvases: a node graph over the generation routes.
--
-- A canvas is a document, not a second queue. It stores nodes and edges as JSON
-- and a run record stores which node produced which job. Executing a canvas is
-- still executing ordinary generation jobs, with the same adapters, the same
-- cost records and the same failure reporting; the graph only decides the order
-- and what feeds what.

CREATE TABLE canvases (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id  TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    nodes_json          TEXT NOT NULL DEFAULT '[]',
    edges_json          TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    deleted_at          TEXT
);

CREATE INDEX idx_canvases_workspace ON canvases (workspace_id, deleted_at, updated_at DESC);

CREATE TABLE canvas_runs (
    id                  TEXT PRIMARY KEY,
    canvas_id           TEXT NOT NULL REFERENCES canvases (id) ON DELETE CASCADE,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id  TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- queued | running | completed | failed | cancelled
    status              TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    -- Per node: status, the job it queued, and the asset it produced.
    node_states_json    TEXT NOT NULL DEFAULT '{}',
    order_json          TEXT NOT NULL DEFAULT '[]',
    error               TEXT NOT NULL DEFAULT '',
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_canvas_runs_canvas ON canvas_runs (canvas_id, created_at DESC);
CREATE INDEX idx_canvas_runs_workspace ON canvas_runs (workspace_id, created_at DESC);
