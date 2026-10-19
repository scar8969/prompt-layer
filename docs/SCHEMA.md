# Database schema

Source of truth: the six SQL files in `studio/migrations/`, applied in filename
order by `Database.migrate()` in `studio/db.py`. The runner also creates one
table of its own, `schema_migrations`, before applying anything.

After a fresh migration the database contains 26 tables:

```
api_connections   assets             audit_log          auth_tokens
character_references   characters    connection_tests   generation_jobs
generation_outputs     idempotency_keys                 job_events
job_temp_files         models        preset_favorites   presets
project_assets         projects      rate_limits        saved_prompts
schema_migrations      sessions      usage_records      users
worker_heartbeats      workspace_members                workspaces
```

## Dependency order (ER-style)

An arrow means "the row on the left stores a foreign key to the row on the
right", so the right-hand table must exist first.

```
users
  <- workspaces.owner_user_id
  <- workspace_members.user_id
  <- sessions.user_id
  <- auth_tokens.user_id
  <- audit_log.user_id
  <- usage_records.user_id

workspaces
  <- workspace_members.workspace_id
  <- sessions.workspace_id
  <- api_connections.workspace_id
  <- models.workspace_id
  <- connection_tests.workspace_id
  <- projects.workspace_id
  <- assets.workspace_id
  <- generation_jobs.workspace_id
  <- generation_outputs.workspace_id
  <- presets.workspace_id           (nullable: NULL is a platform built-in)
  <- saved_prompts.workspace_id
  <- characters.workspace_id
  <- usage_records.workspace_id
  <- audit_log.workspace_id         (nullable)
  <- idempotency_keys.workspace_id

api_connections
  <- models.connection_id           (nullable)
  <- connection_tests.connection_id
  <- generation_jobs.connection_id  (nullable)

models
  <- generation_jobs.model_id       (nullable)

projects
  <- assets.project_id              (nullable, ON DELETE SET NULL)
  <- project_assets.project_id
  <- generation_jobs.project_id     (nullable, ON DELETE SET NULL)

assets
  <- assets.parent_asset_id         (self-reference, nullable)
  <- assets.cover... (projects.cover_asset_id, no FK constraint)
  <- project_assets.asset_id
  <- generation_outputs.asset_id    (nullable, ON DELETE SET NULL)
  <- character_references.asset_id

generation_jobs
  <- generation_outputs.job_id
  <- job_events.job_id
  <- job_temp_files.job_id
  <- usage_records.job_id           (nullable, ON DELETE SET NULL)

presets
  <- preset_favorites.preset_id

characters
  <- characters.parent_character_id (self-reference, nullable)
  <- character_references.character_id

(no incoming edges)
rate_limits, worker_heartbeats, idempotency_keys (workspace only)
```

## Conventions used by every table

| convention | meaning |
|---|---|
| `id TEXT PRIMARY KEY` | a UUID4 string from `studio.util.new_id()`. Not an integer, so ids are not enumerable. |
| `created_at`, `updated_at` `TEXT NOT NULL` | fixed-width UTC strings from `now_iso()`, e.g. `2026-10-17T08:32:15.123456Z`. Fixed width so ORDER BY over the text column is chronological. |
| `*_json` `TEXT NOT NULL DEFAULT '{}'` | a serialised JSON object or array. SQLite has no JSON column type. |
| `INTEGER NOT NULL DEFAULT 0/1` | a boolean. There is no BOOLEAN type in SQLite. |
| `deleted_at TEXT` | soft delete. NULL means live. Queries filter on it. |
| `CHECK (x IN (...))` | the portable stand-in for an enum type. |

---

## 0001_identity.sql

### users

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | account id |
| email | TEXT | NOT NULL | the address as typed, kept for display |
| email_normalized | TEXT | NOT NULL UNIQUE | lowercased address; carries the unique index so lookup is plain equality, not a function per row |
| display_name | TEXT | NOT NULL DEFAULT '' | |
| password_hash | TEXT | NOT NULL | scrypt hash with its parameters stored inside the string |
| is_admin | INTEGER | NOT NULL DEFAULT 0 | instance administrator, not workspace role |
| is_active | INTEGER | NOT NULL DEFAULT 1 | 0 blocks sign-in without deleting history |
| failed_login_count | INTEGER | NOT NULL DEFAULT 0 | lockout counter |
| locked_until | TEXT | | NULL means not locked |
| last_login_at | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |

Index: `idx_users_email_normalized (email_normalized)`.

### workspaces

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | every other row is scoped to this |
| name | TEXT | NOT NULL | |
| slug | TEXT | NOT NULL UNIQUE | |
| owner_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| is_personal | INTEGER | NOT NULL DEFAULT 1 | 1 for the workspace registration creates |
| billing_mode | TEXT | NOT NULL DEFAULT 'user_provider' CHECK IN ('user_provider','platform_credits') | recorded rather than inferred, so no cost line can be attributed to the wrong party |
| created_at, updated_at | TEXT | NOT NULL | |

Index: `idx_workspaces_owner (owner_user_id)`.

### workspace_members

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| role | TEXT | NOT NULL CHECK IN ('owner','admin','editor','viewer') | the per-request authorization source |
| created_at, updated_at | TEXT | NOT NULL | |
| | | UNIQUE (workspace_id, user_id) | one membership per person per workspace |

Index: `idx_workspace_members_user (user_id)`.

### sessions

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | the workspace this session acts in |
| user_agent | TEXT | NOT NULL DEFAULT '' | |
| ip_hash | TEXT | NOT NULL DEFAULT '' | keyed digest, never the address |
| created_at, last_seen_at, expires_at | TEXT | NOT NULL | |
| revoked_at | TEXT | | |

The session **token** is not stored: the cookie holds a random 32-byte value and
this table holds a keyed digest of it, so a database dump yields nothing
replayable.

Indexes: `idx_sessions_user (user_id)`, `idx_sessions_expiry (expires_at)`.

### auth_tokens

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| purpose | TEXT | NOT NULL CHECK IN ('password_reset','email_verify') | |
| token_hash | TEXT | NOT NULL UNIQUE | single-use token, stored hashed |
| expires_at | TEXT | NOT NULL | |
| used_at | TEXT | | non-NULL means already spent |
| created_at | TEXT | NOT NULL | |

Index: `idx_auth_tokens_user (user_id, purpose)`.

---

## 0002_connections.sql

### api_connections

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| created_by_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| name | TEXT | NOT NULL | |
| provider_slug | TEXT | NOT NULL DEFAULT 'custom' | informational label |
| adapter_type | TEXT | NOT NULL CHECK IN ('fal_queue','openai_images','declarative_http','mock') | which protocol the adapter speaks |
| base_url | TEXT | NOT NULL | re-validated against the egress policy on every request, not just on save |
| auth_scheme | TEXT | NOT NULL DEFAULT 'bearer' CHECK IN ('bearer','header','query','none') | |
| auth_header_name | TEXT | NOT NULL DEFAULT 'Authorization' | |
| credential_ciphertext | TEXT | | AES-256-GCM envelope; the plaintext exists in memory for one request |
| credential_key_id | TEXT | | public fingerprint of the key that sealed it, for rotation |
| credential_last4 | TEXT | | the only part ever shown back |
| credential_fingerprint | TEXT | | public, non-reversible |
| extra_headers_json | TEXT | NOT NULL DEFAULT '{}' | non-secret headers only; secret-bearing names are rejected on write |
| config_json | TEXT | NOT NULL DEFAULT '{}' | declarative mapping, validated against a fixed schema, never executed |
| allowed_hosts_json | TEXT | NOT NULL DEFAULT '[]' | hosts this connection may reach beyond the default egress policy |
| is_enabled | INTEGER | NOT NULL DEFAULT 1 | |
| last_test_status | TEXT | | |
| last_test_at | TEXT | | |
| last_test_detail_json | TEXT | NOT NULL DEFAULT '{}' | |
| last_used_at | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |
| deleted_at | TEXT | | soft delete; the credential is cleared on delete |
| | | UNIQUE (workspace_id, name) | |

Index: `idx_connections_workspace (workspace_id, deleted_at)`.

### models

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| connection_id | TEXT | REFERENCES api_connections(id) ON DELETE CASCADE | NULL is allowed by the schema; the connection wizard always sets it |
| model_key | TEXT | NOT NULL | the identifier sent on the wire |
| display_name | TEXT | NOT NULL | |
| provider_slug | TEXT | NOT NULL DEFAULT 'custom' | |
| modality | TEXT | NOT NULL CHECK IN ('image','video','other') | |
| modes_json | TEXT | NOT NULL DEFAULT '[]' | validated on write against the modality |
| capabilities_json | TEXT | NOT NULL DEFAULT '{}' | |
| parameter_schema_json | TEXT | NOT NULL DEFAULT '{}' | |
| defaults_json | TEXT | NOT NULL DEFAULT '{}' | |
| limits_json | TEXT | NOT NULL DEFAULT '{}' | |
| source | TEXT | NOT NULL DEFAULT 'manual' CHECK IN ('discovered','manual') | whether a provider listed it or a human typed it |
| is_enabled | INTEGER | NOT NULL DEFAULT 1 | |
| is_favorite | INTEGER | NOT NULL DEFAULT 0 | |
| is_default | INTEGER | NOT NULL DEFAULT 0 | |
| last_verified_at | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |
| | | UNIQUE (connection_id, model_key) | one row per provider model per connection |

Indexes: `idx_models_workspace (workspace_id, modality, is_enabled)`,
`idx_models_connection (connection_id)`.

### connection_tests

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| connection_id | TEXT | NOT NULL REFERENCES api_connections(id) ON DELETE CASCADE | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| outcome | TEXT | NOT NULL | one of the connection-test codes in `studio/errors.py` |
| http_status | INTEGER | | |
| latency_ms | INTEGER | | |
| detail_json | TEXT | NOT NULL DEFAULT '{}' | |
| created_at | TEXT | NOT NULL | |

Kept as history so "last tested" is an observation with a timestamp, not a flag
somebody set once.

Index: `idx_connection_tests_connection (connection_id, created_at DESC)`.

---

## 0003_content.sql

### projects

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| created_by_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| name | TEXT | NOT NULL | |
| description | TEXT | NOT NULL DEFAULT '' | |
| cover_asset_id | TEXT | | **no foreign key constraint**; nothing enforces that the asset exists |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN ('active','archived') | |
| tags_json | TEXT | NOT NULL DEFAULT '[]' | |
| created_at, updated_at | TEXT | NOT NULL | |
| deleted_at | TEXT | | |

Index: `idx_projects_workspace (workspace_id, deleted_at)`.

### assets

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| owner_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| project_id | TEXT | REFERENCES projects(id) ON DELETE SET NULL | |
| kind | TEXT | NOT NULL CHECK IN ('image','video','audio','other') | |
| source | TEXT | NOT NULL CHECK IN ('upload','generated','derived') | user-supplied, provider output, or made locally from another asset |
| storage_key | TEXT | NOT NULL | the object key, not the bytes |
| thumbnail_key | TEXT | | |
| filename | TEXT | NOT NULL DEFAULT '' | |
| mime_type | TEXT | NOT NULL | taken from the sniffed bytes, not the upload's claim |
| byte_size | INTEGER | NOT NULL DEFAULT 0 | |
| checksum | TEXT | NOT NULL DEFAULT '' | |
| width, height | INTEGER | | NULL when the probe could not read them |
| duration_seconds | REAL | | NULL for still images |
| frame_rate | REAL | | |
| reference_role | TEXT | | carried so a reused asset keeps its meaning in a new job |
| metadata_json | TEXT | NOT NULL DEFAULT '{}' | |
| parent_asset_id | TEXT | REFERENCES assets(id) ON DELETE SET NULL | derives-from link |
| is_favorite | INTEGER | NOT NULL DEFAULT 0 | |
| deleted_at | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |

Indexes: `idx_assets_workspace (workspace_id, deleted_at, created_at DESC)`,
`idx_assets_project (project_id, deleted_at)`,
`idx_assets_kind (workspace_id, kind, deleted_at)`.

### project_assets

| column | type | constraints | why |
|---|---|---|---|
| project_id | TEXT | NOT NULL REFERENCES projects(id) ON DELETE CASCADE | |
| asset_id | TEXT | NOT NULL REFERENCES assets(id) ON DELETE CASCADE | |
| added_at | TEXT | NOT NULL | |
| | | PRIMARY KEY (project_id, asset_id) | |

Membership by join table, so an asset can sit in a project without leaving the
library that owns it.

---

## 0004_jobs.sql

### generation_jobs

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| created_by_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| project_id | TEXT | REFERENCES projects(id) ON DELETE SET NULL | |
| connection_id | TEXT | REFERENCES api_connections(id) ON DELETE SET NULL | |
| model_id | TEXT | REFERENCES models(id) ON DELETE SET NULL | |
| modality | TEXT | NOT NULL CHECK IN ('image','video') | |
| mode | TEXT | NOT NULL | a mode key from `studio/adapters/base.py` |
| prompt | TEXT | NOT NULL DEFAULT '' | |
| negative_prompt | TEXT | NOT NULL DEFAULT '' | |
| parameters_json | TEXT | NOT NULL DEFAULT '{}' | |
| request_json | TEXT | NOT NULL DEFAULT '{}' | snapshot of the resolved request, so history stays reproducible after the model's schema is edited |
| references_json | TEXT | NOT NULL DEFAULT '[]' | |
| status | TEXT | NOT NULL CHECK IN ('draft','queued','validating','submitting','processing','downloading','completed','failed','cancelled','timed_out') | |
| status_message | TEXT | NOT NULL DEFAULT '' | |
| provider_job_ref | TEXT | | |
| attempt | INTEGER | NOT NULL DEFAULT 0 | |
| max_attempts | INTEGER | NOT NULL DEFAULT 3 | |
| next_attempt_at | TEXT | | |
| lease_owner | TEXT | | worker holding the job |
| lease_expires_at | TEXT | | so two workers cannot drive one job and double-bill it |
| cancel_requested_at | TEXT | | |
| submitted_at, started_at, finished_at | TEXT | | |
| error_code, error_message, error_hint | TEXT | | code, sentence, next step |
| provider_error_json | TEXT | | the provider's own words, scrubbed |
| cost_estimate_json | TEXT | NOT NULL DEFAULT '{}' | estimates only; never a fabricated actual |
| output_count | INTEGER | NOT NULL DEFAULT 0 | |
| idempotency_key | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |

Indexes: `idx_jobs_workspace (workspace_id, created_at DESC)`,
`idx_jobs_status (status, next_attempt_at)`,
`idx_jobs_project (project_id, created_at DESC)`,
`idx_jobs_user (created_by_user_id, created_at DESC)`,
and a **partial unique index**
`idx_jobs_idempotency (workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL`.

### generation_outputs

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| job_id | TEXT | NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| asset_id | TEXT | REFERENCES assets(id) ON DELETE SET NULL | NULL when the bytes could not be ingested |
| batch_index | INTEGER | NOT NULL DEFAULT 0 | position within one request that asked for several results |
| provider_url | TEXT | | |
| ingest_error | TEXT | | set when the provider returned a link that could not be fetched; the row stays so the failure is visible |
| created_at | TEXT | NOT NULL | |
| | | UNIQUE (job_id, batch_index) | |

Indexes: `idx_outputs_job (job_id)`, `idx_outputs_asset (asset_id)`.

### job_events

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| job_id | TEXT | NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE | |
| status | TEXT | NOT NULL | the state reached |
| message | TEXT | NOT NULL DEFAULT '' | |
| detail_json | TEXT | NOT NULL DEFAULT '{}' | |
| created_at | TEXT | NOT NULL | |

Append-only. This is what makes "it said processing for forty minutes then
failed" answerable after the fact.

Index: `idx_job_events_job (job_id, created_at)`.

### job_temp_files

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| job_id | TEXT | NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE | |
| path | TEXT | NOT NULL | |
| created_at | TEXT | NOT NULL | |
| removed_at | TEXT | | NULL until the sweep removed it |

Index: `idx_job_temp_files_job (job_id)`.

---

## 0005_library.sql

### presets

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | REFERENCES workspaces(id) ON DELETE CASCADE | **nullable**: NULL marks a platform built-in visible to every workspace |
| created_by_user_id | TEXT | REFERENCES users(id) ON DELETE SET NULL | |
| slug | TEXT | NOT NULL | |
| kind | TEXT | NOT NULL CHECK IN ('image','video') | |
| name | TEXT | NOT NULL | |
| description | TEXT | NOT NULL DEFAULT '' | |
| category | TEXT | NOT NULL DEFAULT 'general' | |
| thumbnail_url | TEXT | | |
| prompt_template | TEXT | NOT NULL DEFAULT '' | |
| negative_prompt_template | TEXT | NOT NULL DEFAULT '' | |
| defaults_json | TEXT | NOT NULL DEFAULT '{}' | exactly the fields the preset sets, so applying it does not silently overwrite anything else |
| supported_modes_json | TEXT | NOT NULL DEFAULT '[]' | |
| required_references_json | TEXT | NOT NULL DEFAULT '[]' | |
| camera_settings_json | TEXT | NOT NULL DEFAULT '{}' | |
| tags_json | TEXT | NOT NULL DEFAULT '[]' | |
| is_builtin | INTEGER | NOT NULL DEFAULT 0 | |
| created_at, updated_at | TEXT | NOT NULL | |
| | | UNIQUE (workspace_id, slug) | |

Indexes: `idx_presets_kind (kind, workspace_id)`, `idx_presets_category (kind, category)`.

### preset_favorites

| column | type | constraints | why |
|---|---|---|---|
| user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | favourites are per person, not per workspace |
| preset_id | TEXT | NOT NULL REFERENCES presets(id) ON DELETE CASCADE | |
| created_at | TEXT | NOT NULL | |
| | | PRIMARY KEY (user_id, preset_id) | |

### saved_prompts

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| created_by_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| title | TEXT | NOT NULL | |
| body | TEXT | NOT NULL | |
| negative_body | TEXT | NOT NULL DEFAULT '' | |
| variables_json | TEXT | NOT NULL DEFAULT '[]' | declarative `{{variable}}` declarations |
| tags_json | TEXT | NOT NULL DEFAULT '[]' | |
| modality | TEXT | CHECK IN ('image','video') | nullable |
| use_count | INTEGER | NOT NULL DEFAULT 0 | |
| last_used_at | TEXT | | |
| created_at, updated_at | TEXT | NOT NULL | |

Index: `idx_saved_prompts_workspace (workspace_id, updated_at DESC)`.

### characters

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| created_by_user_id | TEXT | NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| name | TEXT | NOT NULL | |
| description | TEXT | NOT NULL DEFAULT '' | |
| attributes_json | TEXT | NOT NULL DEFAULT '{}' | |
| prompt_details | TEXT | NOT NULL DEFAULT '' | |
| preferred_models_json | TEXT | NOT NULL DEFAULT '[]' | |
| tags_json | TEXT | NOT NULL DEFAULT '[]' | |
| version | INTEGER | NOT NULL DEFAULT 1 | |
| parent_character_id | TEXT | REFERENCES characters(id) ON DELETE SET NULL | versioning by chaining, so a working reference set is not destroyed when the character is refined |
| created_at, updated_at | TEXT | NOT NULL | |
| deleted_at | TEXT | | |

Index: `idx_characters_workspace (workspace_id, deleted_at)`.

### character_references

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| character_id | TEXT | NOT NULL REFERENCES characters(id) ON DELETE CASCADE | |
| asset_id | TEXT | NOT NULL REFERENCES assets(id) ON DELETE CASCADE | |
| role | TEXT | NOT NULL DEFAULT 'identity' | |
| position | INTEGER | NOT NULL DEFAULT 0 | ordering matters for a reference set |
| notes | TEXT | NOT NULL DEFAULT '' | |
| created_at | TEXT | NOT NULL | |

Index: `idx_character_references_character (character_id, position)`.

---

## 0006_operations.sql

### usage_records

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| user_id | TEXT | REFERENCES users(id) ON DELETE SET NULL | |
| job_id | TEXT | REFERENCES generation_jobs(id) ON DELETE SET NULL | |
| connection_id | TEXT | REFERENCES api_connections(id) ON DELETE SET NULL | |
| model_id | TEXT | REFERENCES models(id) ON DELETE SET NULL | |
| billing_party | TEXT | NOT NULL CHECK IN ('user_provider','platform') | who paid, recorded per row so a report cannot mix the two |
| modality | TEXT | NOT NULL DEFAULT 'image' | |
| units_json | TEXT | NOT NULL DEFAULT '{}' | units actually observed: outputs, bytes, seconds |
| estimated_cost_json | TEXT | NOT NULL DEFAULT '{}' | |
| actual_cost_json | TEXT | | **nullable on purpose**: an absent actual stays NULL rather than becoming 0 |
| created_at | TEXT | NOT NULL | |

Indexes: `idx_usage_workspace (workspace_id, created_at DESC)`, `idx_usage_job (job_id)`.

### audit_log

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | REFERENCES workspaces(id) ON DELETE SET NULL | |
| user_id | TEXT | REFERENCES users(id) ON DELETE SET NULL | |
| action | TEXT | NOT NULL | |
| target_type | TEXT | NOT NULL DEFAULT '' | |
| target_id | TEXT | NOT NULL DEFAULT '' | |
| request_id | TEXT | NOT NULL DEFAULT '' | joins the row to a log line |
| ip_hash | TEXT | NOT NULL DEFAULT '' | keyed digest, never the address |
| detail_json | TEXT | NOT NULL DEFAULT '{}' | |
| created_at | TEXT | NOT NULL | |

Indexes: `idx_audit_workspace (workspace_id, created_at DESC)`, `idx_audit_action (action, created_at DESC)`.

### rate_limits

| column | type | constraints | why |
|---|---|---|---|
| bucket | TEXT | NOT NULL | opaque, e.g. `ip:<sha256>:login`; hashed where it contains an address |
| window_start | INTEGER | NOT NULL | epoch second floor |
| count | INTEGER | NOT NULL DEFAULT 0 | |
| | | PRIMARY KEY (bucket, window_start) | |

A fixed-window counter in the database, so the limit survives a restart and is
shared between the web process and the workers. It is **not** distributed: two
processes using the same SQLite file share the counts, two processes using
different files do not.

### idempotency_keys

| column | type | constraints | why |
|---|---|---|---|
| key | TEXT | PRIMARY KEY | |
| workspace_id | TEXT | NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE | |
| user_id | TEXT | REFERENCES users(id) ON DELETE SET NULL | |
| scope | TEXT | NOT NULL | which operation the key belongs to |
| request_hash | TEXT | NOT NULL DEFAULT '' | so the same key with a different body is detectable |
| response_json | TEXT | | the stored reply |
| status_code | INTEGER | | |
| created_at | TEXT | NOT NULL | |
| expires_at | TEXT | NOT NULL | |

Index: `idx_idempotency_expiry (expires_at)`.

### worker_heartbeats

| column | type | constraints | why |
|---|---|---|---|
| id | TEXT | PRIMARY KEY | |
| worker_id | TEXT | NOT NULL | |
| status | TEXT | NOT NULL DEFAULT 'idle' | |
| current_job_id | TEXT | | |
| started_at | TEXT | NOT NULL | |
| heartbeat_at | TEXT | NOT NULL | |

Lets a health check say whether generation is actually being processed rather
than merely configured. Note that no health route reads this table today - see
`docs/API.md`.

Index: `idx_worker_heartbeats_worker (worker_id)`.

### schema_migrations (created by the runner, not by a migration file)

| column | type | constraints |
|---|---|---|
| version | TEXT | PRIMARY KEY |
| filename | TEXT | NOT NULL |
| checksum | TEXT | NOT NULL |
| applied_at | TEXT | NOT NULL |

`Database.migrate()` records the sha256 of each file and refuses to run if an
already-applied migration has been edited since.

---

## Portability notes: SQLite versus PostgreSQL

The schema is ordinary SQL, but SQLite is the only database that has been
exercised. These are the concrete differences a move to PostgreSQL has to
handle. None of them are hypothetical.

| area | SQLite (as written) | PostgreSQL |
|---|---|---|
| Identifiers | `TEXT PRIMARY KEY` holding a UUID4 string from `new_id()` | either keep `TEXT` (no change) or move to `uuid` with `gen_random_uuid()`; `uuid` rejects arbitrary strings, so the app-side generator must be kept or every insert path changed |
| Booleans | `INTEGER NOT NULL DEFAULT 0/1`. `is_enabled`, `is_admin`, `is_favorite`, `is_builtin`, `is_personal` | a real `boolean` type; every read that expects `0`/`1` and every write that sends an int must be converted, and `_shape_row` in `studio/repository.py` currently converts integers to booleans |
| JSON | `*_json TEXT NOT NULL DEFAULT '{}'`, serialised by `studio.util.json_dumps` | `jsonb` would allow indexing and querying; keeping `text` is the lowest-risk port, but then nothing gained |
| Enums | `CHECK (col IN (...))` on `adapter_type`, `auth_scheme`, `modality`, `status`, `role`, `purpose`, `kind`, `source`, `billing_mode`, `billing_party` | a real `ENUM` type, or a lookup table with a foreign key. A `CHECK` also works; converting to `ENUM` makes adding a member a migration |
| Floating point | `REAL` for `duration_seconds` and `frame_rate` | `double precision`. `REAL` is a 4-byte float in PostgreSQL, so a straight type mapping would silently lose precision; the portable choice is `double precision` |
| Timestamps | fixed-width ISO-8601 `TEXT`; SQL ordering is lexicographic and therefore chronological | could be `timestamptz`, but then 40-odd comparisons against `now_iso()` strings and `iso_in(seconds)` have to be rewritten. Keeping `TEXT` keeps the code identical |
| Auto-increment | no `AUTOINCREMENT` anywhere; ids are generated in Python | no `SERIAL`/`IDENTITY` needed either, for the same reason |
| Case-insensitive lookup | `email_normalized` pre-lowercased by the application | `citext` exists but is an extension; the explicit column is the portable choice |
| Partial index | `CREATE UNIQUE INDEX idx_jobs_idempotency ... WHERE idempotency_key IS NOT NULL` | PostgreSQL supports partial indexes with the same syntax, so this one transfers unchanged. It is listed only because "create index with a WHERE clause" is not universal across engines |
| `INSERT OR IGNORE` / upsert | not used; inserts go through `Database.insert`, and conflicts raise | `ON CONFLICT` is available and would be the natural replacement if an upsert is ever needed |
| Case sensitivity of names | SQLite is case-insensitive for identifiers | PostgreSQL folds unquoted identifiers to lower case; every table and column name here is already lower case, so this is a non-issue |
| Connection model | thread-local `sqlite3.Connection`, WAL, `busy_timeout`, `BEGIN IMMEDIATE` for writes | a connection pool. `Database.write_tx()` and the re-entrant `Transaction` would need a real implementation over a pool, or a transaction-per-request session |
| Migration runner | `sqlite3.complete_statement` splits the file into statements | that helper is SQLite-specific; splitting would need another tokenizer (or `psycopg`'s multi-statement `execute`) |
| Concurrency | one writer at a time; documented as risk R7 in `docs/PLAN.md` | row-level locking, which removes the contention but changes the retry assumptions in `studio/services/runner.py` |

The claim the code makes, and the claim a reader should hold it to, is only
this: the queries avoid SQLite-only syntax wherever a portable form was just as
clear, so the change is a connection layer rather than a rewrite. Nothing in
the repository has been run against PostgreSQL.
