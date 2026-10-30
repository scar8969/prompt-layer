# Changelog

All notable changes to this repository, with the date and the scope of
the change. The repo's two products are siblings: **Prompt Layer**
(`/api/` + `index.html`, the Vercel-shaped preflight tool) and
**Studio** (`studio/` + `web/`, the BYOK creative platform).

## Unreleased

### One product: the two front doors are merged

The repository shipped two apps that shared a dataset and nothing else: a static
preflight page served at `/`, and the React studio at `/studio`. A session, a
navigation and a model registry stopped at the boundary between them, so the one
question this product exists to answer ("what will this model do to my prompt?")
was invisible from the screen where the prompt was actually sent.

Merged into one application:

- **`studio/routes/prompt.py`** — the layer dataset as four authenticated routes:
  `GET /api/prompt/models`, `GET /api/prompt/models/{id}`, `GET /api/prompt/catalog`,
  `POST /api/prompt/preflight`, plus `GET /api/prompt/modes` for the generation-mode
  vocabulary. The dataset and the assembly logic are `promptlayer/`, unchanged.
- **`GET /api/prompt/catalog`** — the join the merge makes possible: every endpoint
  this platform can drive with a user's key, with the layer facts documented for it.
  `prompt_model` is `null` for the endpoints no document describes, and the client
  renders that absence as unknown rather than as "adds nothing" (18 of 22 documented).
- **Prompt Lab** (`/prompt`) — model picker, prompt, the provider's own off-switches,
  and the assembled result: forwarded prompt, every active layer with its evidence
  and source, resolved parameters with their source, conditional layers, the
  full-control patch, and the request payload.
- **The connection wizard** now starts from the documented catalogue: pick a model by
  name, paste your key, and the endpoint id, modality, modes and controls are filled
  in from the same documents the Prompt Lab reads. A provider's base URL is
  pre-filled from its adapter instead of being remembered by the user.
- **The model inspector** shows the prompt layers documented for a registered model,
  joined on the endpoint id, with a link into the Prompt Lab.

### The contract between the client and the server, made checkable

The three bugs above shared one cause: the client read field names the server never
sends, and nothing compared the two. A missing field in JavaScript is `undefined` --
rendered as a blank, a zero or an empty list -- so the failures were silent until
somebody used the screen.

Measured, not guessed: `scripts/dump_shapes.py` prints the real payload of every
resource. It found the drift in eleven readers.

| Reader | Asked for | The server sends |
|---|---|---|
| `readPreset` | `modality`, `parameters`, `camera`, `aspect_ratio`, `key` | `kind`, `defaults`, `camera_settings`, `slug` |
| `readPrompt` | `text`, `negative_prompt`, `usage_count` | `body`, `negative_body`, `use_count` |
| `readCharacter` | `default_prompt`, `preferred_model_id`, `reference_asset_ids` | `prompt_details`, `preferred_models`, `references` |
| `readJob` | nested `model`, `state`, `message`, `error`, `kind`, `count` | `model` (absent), `status`, `status_message`, `error_code`/`error_message`, `modality`, `output_count` |
| `readAsset` | `name`, `content_type`, `job_id`, `is_mock` | `filename`, `mime_type` (an asset is not a job) |
| `readModel` | a nested `provider` block | `connection_name`, `connection_adapter_type`, `provider_name` |
| `readProject` | `job_count`, `is_favorite`, `is_archived` | `status`, `asset_count` |
| `readUser` / `readWorkspace` / `readConnection` / `readSystemStatus` | `is_active`, `created_at`, `slug`, `adapter_label`, `storage` | `is_admin`, `role`, `provider_slug`, `storage_backend` |

Fixed on the server, because the payload was the poorer one:

- **A job now carries its model and its provider.** `_shape.job_with_provider` builds
  a `model` block from the registry, so job cards and the job header can name what
  ran. Deleting a model still leaves the history readable.
- **A character's detail carries `reference_count`,** the field the list projection
  has, so one reader serves both.
- **Library creates answer in the list's shape.** `POST /api/presets`,
  `/api/prompts` and `/api/characters` returned raw rows, echoing `*_json` keys that
  no list route sends.
- **A built-in preset is now exactly a saved preset plus `is_builtin`.** `kind`,
  `defaults`, `camera_settings`, `supported_modes`, `required_references`, `slug`.
  The dataset's `aspect_ratio` rides in `defaults`, because the frame is part of the
  look rather than a model parameter.

Fixed on the client, in one place each:

- Every reader now reads the keys the payload carries and translates them to the
  view model the screens were written against. No screen knows the wire names.
- Writes go through `presetBody`, `promptBody`, `characterBody`, which map the form
  to the keys the route accepts. The preset form used to send `modality` to a route
  that accepts `kind`, so **no preset could be saved from the UI at all**.
- A preset's `defaults` are filtered against the selected model's schema when
  applied, and `defaults.aspect_ratio` goes to the frame control.
- `UsageRow` reads `actual_cost` / `estimated_cost` / `units` instead of the
  `quantity` / `unit` / `is_estimate` fields it invented.

Guarded, so it cannot silently regress:

- `tests/test_client_contract.py` parses every `readX(raw)` in
  `web/src/api/endpoints.ts`, hits a live server for the same resource, and fails
  naming any field the client reads and the payload does not carry. It also parses
  the three write builders against the repository's accepted-key whitelists, and
  names the deliberate exceptions (a slug is derived from the name; optional lists
  default to empty).
- `tests/test_job_vocabulary.py` pins the status/state translation against the
  database's CHECK constraint.
- `scripts/dump_shapes.py` prints the shape of every resource, for the next time a
  screen shows a blank.

### The loop itself: preset -> key -> model -> generation

The merge above is plumbing. Exercising the actual product loop — pick a look,
generate on a model you brought a key for — found three defects that made it not
work, none of which any existing test could see:

1. **A built-in preset arrived without an `id`, and its negative prompt under the
   dataset's own key.** `GET /api/presets` returned the raw catalogue entry
   (`key`, `negative_template`) next to saved rows (`id`,
   `negative_prompt_template`), so 40 image and 30 video presets could not be
   selected reliably and never filled the negative-prompt field. Both sources now
   come back in one shape from `studio/routes/library.py:_builtin_preset`;
   `tests/test_preset_loop.py` pins the id, the negative prompt and `is_builtin`,
   and the library screen no longer offers edit/delete/favourite for a document
   that is not a row.

2. **Applying a preset was a 400, not a generation.** Every catalogue preset
   carries `cfg_scale`, `steps` and `sampler`; a fal or OpenAI model does not
   declare them, and the backend refuses a parameter the model does not declare —
   correctly. The studio now filters a preset against the selected model's
   parameter schema and says which settings it left out, and re-filters when the
   preset's own model is selected and its schema arrives.

3. **A finished job still read as `queued` in the client.** The server stores
   `status` (`queued` … `completed`); the screens read `state` and every terminal
   check in the client is written against `succeeded`. Reading a field that did
   not exist made every job look queued forever: the poller never stopped and the
   submit button never came back. One translation table now lives in
   `web/src/api/endpoints.ts`, used for reads, for the events list and for the
   Jobs screen filter, and `tests/test_job_vocabulary.py` parses the database's
   CHECK constraint and fails when the server grows a status the client has not
   translated.

Also added: the preset library hands a look to a studio (`Generate this` →
`/image?preset=<id>`), and the studio applies it on load, filtered and with the
preset's own model selected when it names one.

Fixed on the way, both blocking the "bring your own key" path:

- **The wizard offered three modes the service rejects** (`generate`, `edit`,
  `transform`). Any model registered through the UI failed with *"not a generation
  mode"*, and the default selection was one of them. The vocabulary now comes from
  `studio/adapters/base.py` through `GET /api/prompt/modes`, so the form cannot drift
  from the validator.
- **`api/studio.py` excluded `promptlayer/**`** from its bundle. It now imports that
  package, so the exclusion would have 404'd every Prompt Lab route on the deployment
  while working locally. `tests/test_deploy.py` pins it.

Retired:

- **`index.html`** (the static page) and **`tests/test_ui.py`** (its key-field tests).
  The dataset is served from the application, and the equivalence that mattered —
  the key never being stored client-side — is covered by the connection tests.
- The root is no longer a static file: `/((?!api/).*)` rewrites to the one function,
  which serves `web/dist` for every non-API path. `dev.py` serves `web/dist` too.

### The deployment build: three things a local test cannot see

`vercel build --prod --yes` was run for the first time against the Studio
function, and the built output was inspected rather than the configuration.
Three problems surfaced that no test in the suite could have found, because
locally the whole working tree is present:

1. **`var/` was published.** It holds `studio.db`, `.studio-encryption-key`
   and `.studio-auth-secret`. The encryption key opens every sealed provider
   credential in the database, so this was a disclosure. Both layers now
   exclude it: `.vercelignore` for the static layer, and `var/**` in every
   function's `excludeFiles`.

2. **The function bundle had no `psycopg` and no `cryptography`.** The platform
   prefers `uv.lock` over `requirements.txt` when both exist, and the lock
   declared no dependencies, so the bundle shipped `pip` and nothing else.
   `studio/crypto.py` would have raised `EncryptionUnavailable` when a
   credential was stored, and `studio/db.py` would have raised
   `MigrationError` when `STUDIO_DATABASE_URL` pointed at Postgres. The three
   packages are now declared in `pyproject.toml`, which is what the lock is
   built from. `requirements.txt` stays empty on purpose -- it is what stops
   the platform detecting a framework preset.

   **This also changes what `pyproject.toml` and `uv.lock` are.** They were
   ignored as build artifacts the CLI wrote, describing a project called
   "app" with no dependencies. Now they are the deployment's dependency list,
   so they are tracked in git and uploaded. `tests/test_deploy.py` pins that,
   because excluding them from the upload would bring the same failure back,
   silently.

3. **`web/dist` was excluded from the function that serves it.** `api/studio.py`
   serves the built client out of `web/dist`, and its `excludeFiles` listed
   `web/dist/**`. Every page request would have 404'd on the deployment while
   working perfectly on the machine that built it.

Also fixed:

- **`.env.example` was never committed.** The `.env*` rule swallowed it, but
  `docs/SETUP.md`, `docs/DEPLOYMENT.md` and `docs/PLAN.md` all tell the reader
  to copy it. It now ships: `!.env.example` in `.gitignore` and in
  `.vercelignore`. The template holds placeholders only -- `replace-me-with-32-bytes-of-hex`
  and localhost URLs -- and `scripts/check_env_template.py` checks that.
- **`.vercelignore` did not exclude `.env*`.** Vercel serves the project
  directory as-is, so a local `.env` on the machine that ran `vercel build`
  would have been downloadable from the deployment's URL.

Added:

- **`scripts/check_built_output.py`** -- reads the built output and reports what
  would actually be published: credential files, the runtime state directory,
  and the third-party packages a function needs. This is the check that found
  all three problems above.
- **`scripts/build_client.py`** -- rebuilds `web/dist` with the deployment's
  mount prefix and fails if the prefix is missing from the bundle or if the
  client calls a path the backend does not route. `web/dist` is committed
  because the deployment has no Node build step.
- **`scripts/harden_exclude_files.py`** -- applies the same credential and
  runtime-state patterns to every function's `excludeFiles`, so a new function
  cannot forget one.
- **`scripts/cleanup_scratch.py`** -- removes the session scratch files, and
  refuses to delete anything git is tracking.

`tests/test_deploy.py` gained five tests covering the three problems above,
the shared exclude pattern, and the dependency declaration.

### Phases 2-5: Studio is deployable to Vercel

Studio now runs as a request-scoped function plus a scheduled tick, instead of
only as a long-lived process. Nothing about the application changed: the same
`HttpApp.handle(RequestContext)` that the socket server calls is what the function
calls, so routing, auth, CSRF, rate limiting, redaction and error envelopes are
the deployed code, not a second implementation.

Verified: **700 tests pass** against Postgres + a live S3-compatible server;
**670 pass with 30 skipped** on SQLite with no S3 endpoint.

Added:

- **`studio/serverless.py`** -- the bridge. `build_app(start_worker=False)` keeps
  worker threads out of a request-scoped process; `make_request` builds the same
  `RequestContext` the socket handler builds; `handle()` returns the same
  `Response`. Also holds `application_path()` (mount-prefix translation) and
  `authorised()` (the cron secret check), both there so they are unit-testable
  without importing a Vercel function file.
- **`api/studio.py`** -- one catch-all function at `/api/studio/*`. One, not
  eight, because splitting by route group would mean re-encoding the same
  prefixes in `vercel.json`, where a mismatch is invisible until a 404 in
  production. The prefix exists because `/api/health`, `/api/models` and
  `/api/presets` already belong to the Prompt Layer functions in this repository.
- **`studio/tick.py`** -- one pass of generation work per invocation, bounded by
  `limit` and `budget_seconds`. A killed invocation leaves a job holding a lease,
  so the tick stops claiming well before `maxDuration` rather than overrunning.
- **`api/studio_cron.py`** -- the scheduled endpoint, guarded by `CRON_SECRET`.
  Unset means it refuses everything: the URL is public, and an open queue is
  other people spending the provider's credit.
- **Phase 5: `VITE_STUDIO_API_PREFIX`.** The React client prefixes every request
  path in one place (`buildUrl`), so 74 route definitions did not need editing.
  Built with `VITE_STUDIO_API_PREFIX=/api/studio npx vite build` and verified the
  prefix is in the bundle.
- **`vercel.json`** -- rewrites for the mount, a cron entry, per-function memory
  and timeouts, and excludes so `web/node_modules` and `web/dist` do not bloat
  the function bundle.

Fixed:

- **Storage now fails closed when it is asked for S3 and cannot do it.** The old
  behaviour fell back to the local disk, which on a serverless platform means
  every upload and every generated file appears to succeed and then vanishes.
  `STUDIO_STORAGE_BACKEND=s3` with settings missing is now a
  `configuration_required` error naming the four variables.
- **`application_path` dropped the platform header when it carried the prefix**,
  which would have routed every request to the raw path. Caught by its own test.

Verified against real services, not assumed:

- **S3 `put`/`get`/`list_prefix`/`signed_url`/`delete` against a live
  S3-compatible server** (moto). `docs/LIMITATIONS.md` had recorded that
  `S3ObjectStore` had never run against a live bucket; it has now.
- **The `Authorization` header matches botocore byte for byte** for PUT, GET and
  DELETE (`scripts/check_sigv4_parity.py`). This matters because moto accepts a
  *wrong* secret on a header-signed request, so the round trip alone does not
  prove the signature.
- **A submitted job reaches `completed` with an output and an asset** driven only
  by ticks: submit over the bridge, tick to submit, tick again to poll
  (`scripts/probe_tick_e2e.py`).


### Phase 1 of the Vercel migration: Studio runs on Postgres

`studio/db.py` now speaks Postgres as well as SQLite, behind the same interface.
Choosing the engine is one value -- `STUDIO_DATABASE_URL` -- and
`Database.from_config()` is the only place that decides. The same seven
migrations apply to both; there is no second schema.

**Verified, not assumed:** the full suite passes on SQLite (639 passed, 18
skipped) *and* against a real PostgreSQL 17 server (657 passed, 0 skipped, 0
failed). Both counts are after the change; the suite was 619 before it.

Added:

- **`studio/sql_dialect.py`** -- the finite list of differences between the
  engines, each with the reason it exists: placeholder style, literal `%`
  doubling (only when psycopg will interpolate), `INSERT OR IGNORE` ->
  `ON CONFLICT DO NOTHING`, a statement splitter that respects comments and
  string literals, and a row builder that works on either driver.
- **`Database(url=...)` and `Database.from_config()`** -- the engine choice, on
  the existing class rather than a parallel one, so the two `isinstance(db,
  Database)` guards and every caller stay unchanged.
- **`StudioIntegrityError`** -- one type for a constraint failure on either
  engine. `_conflict_for` reads the engine's own message, so UNIQUE / CHECK /
  FOREIGN KEY still map to the right error codes.
- **(PG) a bounded connection pool**, `min_size=1`, `max_size=12`, released at
  the end of every HTTP request via `Database.release()`.
- **`tests/test_db_backend.py`** (34 tests) -- dialect translation, statement
  splitting against SQLite's own tokenizer as the reference, row shape, engine
  selection from configuration, migrations on an empty Postgres, write
  round-trips, and a 40-thread connection test.
- **`tests/test_supabase_migrations.py`** -- compares the migrated schema on
  both engines table by table and column by column, and the applied version
  list.
- **`tests/conftest.py`** -- `STUDIO_TEST_POSTGRES` gives each test module its
  own freshly created database and drops it afterwards. Unset means SQLite,
  exactly as before.

Fixed (found by running the suite against Postgres, not by reading it):

- **A job could be handed to two workers at once -- double provider submits,
  double billing.** `claim_next` is `UPDATE ... WHERE id = (SELECT ... LIMIT 1)`.
  On SQLite the surrounding `BEGIN IMMEDIATE` serialises writers, which is what
  the docstring described. On Postgres it does not: the second worker's UPDATE
  blocks on the row lock, and on release Postgres re-checks only the *outer*
  WHERE -- it does not re-run the subquery. With a bare `WHERE id = (...)` there
  was nothing for that re-check to refuse. Measured: **20 jobs, 6 workers, 48
  claims, 28 duplicates.** Fixed with `FOR UPDATE SKIP LOCKED` inside the
  subquery (Postgres only) and the status/lease conditions repeated in the outer
  WHERE. `tests/test_claiming_postgres.py` pins it over three contended rounds;
  the broken statement fails that test 4 runs out of 4.
- **`claim_next` read its `RETURNING` result as a bare psycopg tuple** --
  `TypeError: tuple indices must be integers or slices, not str`. Any
  `INSERT/UPDATE ... RETURNING` read through `cursor.fetchall()` hits this.
  Added `Database.rows(cursor)` / `Transaction.rows(cursor)`, which normalise a
  cursor's result to the same row shape `query()` returns.
- **Worker threads held a pool connection forever.** The HTTP side releases per
  request; a long-lived worker thread did not, so N workers consumed N pool
  slots permanently. `GenerationRunner` now releases per loop iteration.

- **A connection leak that would have taken the site down under load.** The
  server is one thread per request, so a thread-local Postgres handle opened a
  new server connection per request and never closed it. The first Postgres run
  failed with `FATAL: sorry, too many clients already`. Now bounded by a pool
  and released per request.
- **`rowid` does not exist in Postgres** -- two ordering clauses used it. They
  now order by `created_at`, which is the ordering key the rest of the code
  already relies on.
- **`COLLATE NOCASE` is a SQLite collation** -- four `ORDER BY` clauses used it
  and answered HTTP 500 on Postgres. Replaced with `LOWER(...)`, which is plain
  SQL and orders identically.
- **The migration splitter cut statements at a semicolon inside a comment** --
  produced `syntax error at end of input` on the first Postgres migration. The
  splitter now tracks comments, quoted strings and dollar-quoted bodies.
- **`dict(row)` on a psycopg row** -- `ValueError: dictionary update sequence
  element #0`. Rows now come back as `studio.db.Row`, which answers `row["col"]`
  and `row[0]` on either engine.

### Added (this session, across two commits and the merge)

- **`studio/repository.py`** — `AssetRepository.list` now returns
  `(rows, next_cursor)` instead of a bare list, matching its own
  docstring and the peer `JobRepository.list`. The route at
  `studio/routes/assets.py:97` was unpacking as if the cursor was
  present, so any workspace with more than one asset was answering
  HTTP 500 with `ValueError: too many values to unpack`. The cursor
  is a keyset over `(created_at, id)` — the same shape
  `JobRepository` uses — with a `page_size + 1` trick to detect a
  next page.

- **`tests/test_studio.py::test_listing_assets_returns_paged_rows_and_a_cursor`**
  — HTTP-level regression test. Creates two assets in the same
  workspace and exercises `GET /api/assets` and `?kind=image`,
  `?favorite=false`, `?q=png&limit=5`. Pre-fix this was 500; the
  test fails with the same 500 the smoke run produced if the
  unpacking regresses.

- **`tests/test_library.py::test_asset_listing_paginates_with_a_keyset_cursor`**
  — repository-level test. Three assets with distinct `created_at`
  values, cursor walks all three pages in order, terminates. Pins
  the repository's return shape to a tuple so a future "refactor
  back to a list" cannot break the route again.

- **`scripts/gen_studio_env.py`** — one-time helper that generates
  fresh `STUDIO_ENCRYPTION_KEY` and `STUDIO_AUTH_SECRET` and prints
  the export commands. ASCII-safe output.

- **`scripts/run_studio.py`** — applies `.studio_env.sh` to the
  current process and execs `python -m studio.app`. The previous
  version's `shlex.split` was silently dropping every var; this
  version splits on the first `=` and strips quotes, so the env
  is actually applied. As a result, sessions and encrypted
  credentials persist across restarts.

- **`scripts/smoke_studio.py`** — end-to-end smoke (register →
  login → connection → test → model → generation → wait for
  worker → asset stored). Uses only `urllib.request`, no extra
  dependencies. Idempotent.

- **`scripts/repro_assets_list.py`** — minimal repro for the
  unpacking bug, post-fix. Demonstrates the same scenario the
  regression tests use, against a live server.

- **`scripts/audit_log.py`**, **`scripts/audit_summary.py`** —
  pre-push audit helpers.

- **`AUDIT.md`** — pre-push audit report.

- **The full Studio package, web client, Dockerfile, compose,
  deployment docs, and Studio tests** — merged onto `main` from the
  `studio-initial-pending-review` branch in the second commit.
  This is roughly 42,000 lines across 126 files: the `studio/`
  package (60+ Python modules including 7 migrations, 4 adapters,
  6 security modules, 6 service modules, 9 route groups), the
  `web/` React 19 + TypeScript 7 + Vite 8 client, the deployment
  artifacts, and the eight `docs/*.md` files. `AUDIT.md` records
  what was reviewed deeply and what was reviewed by inspection;
  in particular, no live provider credential has ever been used
  with the `fal_queue` or `openai_images` adapters, and the
  `S3ObjectStore` has never been run against a live bucket. These
  limitations are documented in `docs/LIMITATIONS.md`.

### Changed

- **`.gitignore`** — added `var/`, `web/node_modules/`, `web/dist/`
  (Studio runtime state) and the session artifacts produced by
  interactive use (`.studio_env.sh`, `out.txt`, `smoke_out.txt`,
  the local `git_*.txt` and `audit_*.txt` files).

### Verified

- Full test suite: **619 passed in 61.75 s** after the merge.
