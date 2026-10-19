-- Presets, saved prompts, characters and prompt history: the reusable assets of
-- the creative workflow.

CREATE TABLE presets (
    id                      TEXT PRIMARY KEY,
    -- NULL means a platform built-in visible to every workspace.
    workspace_id            TEXT REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id      TEXT REFERENCES users (id) ON DELETE SET NULL,
    slug                    TEXT NOT NULL,
    kind                    TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    category                TEXT NOT NULL DEFAULT 'general',
    thumbnail_url           TEXT,
    prompt_template         TEXT NOT NULL DEFAULT '',
    negative_prompt_template TEXT NOT NULL DEFAULT '',
    -- Which fields the preset wants to set, and what it sets them to. Applying a
    -- preset surfaces exactly these keys so nothing the user chose is silently
    -- overwritten.
    defaults_json           TEXT NOT NULL DEFAULT '{}',
    supported_modes_json    TEXT NOT NULL DEFAULT '[]',
    required_references_json TEXT NOT NULL DEFAULT '[]',
    camera_settings_json    TEXT NOT NULL DEFAULT '{}',
    tags_json               TEXT NOT NULL DEFAULT '[]',
    is_builtin              INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE INDEX idx_presets_kind ON presets (kind, workspace_id);
CREATE INDEX idx_presets_category ON presets (kind, category);

CREATE TABLE preset_favorites (
    user_id     TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    preset_id   TEXT NOT NULL REFERENCES presets (id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, preset_id)
);

CREATE TABLE saved_prompts (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id  TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    negative_body       TEXT NOT NULL DEFAULT '',
    -- Declarative {{variable}} declarations so a prompt can be a template without
    -- string substitution logic living in the UI.
    variables_json      TEXT NOT NULL DEFAULT '[]',
    tags_json           TEXT NOT NULL DEFAULT '[]',
    modality            TEXT CHECK (modality IN ('image', 'video')),
    use_count           INTEGER NOT NULL DEFAULT 0,
    last_used_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_saved_prompts_workspace ON saved_prompts (workspace_id, updated_at DESC);

CREATE TABLE characters (
    id                      TEXT PRIMARY KEY,
    workspace_id            TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    created_by_user_id      TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    attributes_json         TEXT NOT NULL DEFAULT '{}',
    prompt_details          TEXT NOT NULL DEFAULT '',
    preferred_models_json   TEXT NOT NULL DEFAULT '[]',
    tags_json               TEXT NOT NULL DEFAULT '[]',
    -- A character is versioned by chaining, so a reference set that worked is not
    -- destroyed when the character is refined.
    version                 INTEGER NOT NULL DEFAULT 1,
    parent_character_id     TEXT REFERENCES characters (id) ON DELETE SET NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    deleted_at              TEXT
);

CREATE INDEX idx_characters_workspace ON characters (workspace_id, deleted_at);

CREATE TABLE character_references (
    id              TEXT PRIMARY KEY,
    character_id    TEXT NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    asset_id        TEXT NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'identity',
    position        INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_character_references_character ON character_references (character_id, position);
