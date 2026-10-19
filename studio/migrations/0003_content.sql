-- Projects and assets. Media bytes live in object storage; this is the index
-- over them plus everything the UI needs to render a library without touching
-- the bytes.

CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    cover_asset_id  TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived')),
    tags_json       TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT
);

CREATE INDEX idx_projects_workspace ON projects (workspace_id, deleted_at);

CREATE TABLE assets (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    owner_user_id       TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id          TEXT REFERENCES projects (id) ON DELETE SET NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('image', 'video', 'audio', 'other')),
    -- 'upload' is user-supplied; 'generated' came back from a provider;
    -- 'derived' was produced locally from another asset (a thumbnail, a crop).
    source              TEXT NOT NULL CHECK (source IN ('upload', 'generated', 'derived')),
    storage_key         TEXT NOT NULL,
    thumbnail_key       TEXT,
    filename            TEXT NOT NULL DEFAULT '',
    mime_type           TEXT NOT NULL,
    byte_size           INTEGER NOT NULL DEFAULT 0,
    checksum            TEXT NOT NULL DEFAULT '',
    width               INTEGER,
    height              INTEGER,
    duration_seconds    REAL,
    frame_rate          REAL,
    -- Reference role when this asset is attached to a generation: "main subject",
    -- "pose", "last frame" and so on. Stored here so a reused asset keeps its
    -- meaning when it is dropped into a new job.
    reference_role      TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    parent_asset_id     TEXT REFERENCES assets (id) ON DELETE SET NULL,
    is_favorite         INTEGER NOT NULL DEFAULT 0,
    deleted_at          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_assets_workspace ON assets (workspace_id, deleted_at, created_at DESC);
CREATE INDEX idx_assets_project ON assets (project_id, deleted_at);
CREATE INDEX idx_assets_kind ON assets (workspace_id, kind, deleted_at);

-- Project membership is by asset, so an asset can appear in a project without
-- being moved out of the library that owns it.
CREATE TABLE project_assets (
    project_id  TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    asset_id    TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, asset_id)
);
