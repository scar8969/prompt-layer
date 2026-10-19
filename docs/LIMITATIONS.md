# Limitations

This is the honest list. Every item is something the code does not do, or has
not been proven to do, with the consequence stated. Nothing here is padded and
nothing real is left out.

## Database

### Postgres is verified against a local PostgreSQL 17, not against Supabase

Phase 1 was developed and tested against PostgreSQL 17.6 running locally on
Windows. The full suite passes there (657 tests) and on SQLite (639, with the 14
Postgres-only tests skipped). What that does **not** prove:

- **Nothing has run against Supabase itself.** The pooler behaves differently
  from a direct connection in ways that matter: transaction pooling can move a
  session between server connections, which breaks session-level state. Studio
  opens no `SET`, no advisory locks and no session-level temp tables, so it
  should be unaffected, but that is reasoning rather than measurement. A
  connection string pointed at the direct port (5432) rather than the pooler
  (6543) sidesteps the question entirely and is the safer first deployment.
- **Supabase RLS is not used and must not be assumed.** Studio enforces
  workspace scoping in the repository layer, so the database role needs to read
  across workspaces. A role restricted by RLS will appear to work and then
  return the wrong rows.
- **Connection ceilings were tested at one process.** The pool is capped at 12.
  Several workers behind Supabase's pooler multiply that; the project's own
  `max_connections` and pooler mode are the real limit.
- **No migration has been run against a database that already holds data.**
  Every test migrates an empty database. There is one schema version so far, so
  there is nothing to upgrade *from* yet; the first future migration is the
  first real test of that path.

### SQLite write contention is unchanged

Moving to Postgres removes the single-writer limit, but the SQLite path is still
what a fresh clone uses, and it still serialises writes. That is documented, not
fixed.

## Providers

### No live provider credential has ever been used

The fal adapter (`studio/adapters/fal.py`) was developed and tested against
anonymous endpoints only: the public OpenAPI document at
`https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=...`, the public
catalog at `https://api.fal.ai/v1/models` (used as a cheap key probe because it
validates whatever credential it is given), and 401 responses. **No real
billable generation has ever been submitted.** Consequence: the submit, poll,
download and cancel paths are exercised against the provider's *described*
interface, not its live behaviour. Rate limits, queue-time quirks, response
field drift and billing errors are unverified.

### The OpenAI-compatible adapter is implemented from the public API shape

`studio/adapters/openai.py` registers `OpenAIImagesAdapter` under the
`openai_images` slug. It subclasses the declarative adapter and installs, per
mode, the mapping preset written from the published request and response
shapes (`OPENAI_IMAGES_SPEC`, `OPENAI_IMAGES_EDIT_SPEC`). Its capability
profile is hand-written rather than inferred: three aspect ratios, three
square-or-landscape resolutions, at most four outputs, no negative prompt, no
seed, a mask only on the edit path.

**It has never been run against a live OpenAI key.** Consequence: the request
and response mapping is unverified against the real service, so a renamed or
added field produces a clear failure rather than an image. Polling is
unnecessary because that API is synchronous, which is why the adapter walks
`processing → downloading` in one pass instead of polling.

### The declarative adapter fails rather than guesses

A user-supplied mapping that is wrong produces a job failure with the provider
payload attached, never a plausible-looking output URL. Consequence: a
misconfigured custom provider shows an error where a tolerant implementation
would have shown something. This is intentional and is the reason the feature is
safe to expose.

## Storage

### The S3 storage backend has never been run against a live bucket

`S3ObjectStore` in `studio/storage.py` implements AWS Signature Version 4 with
`hashlib`/`hmac` only (no boto3). Its two signing paths reproduce the worked
examples in the AWS documentation byte for byte, and its error mapping is written
from the published protocol — but **no PUT, HEAD, listing or presigned GET has
ever reached a real bucket.** Consequence: the local backend is the only one that
has been exercised, and it is the default. Do not select `s3` until somebody has
pointed it at a bucket and watched those four calls succeed.

The local backend also refuses a body larger than `MAX_OBJECT_BYTES`
(512 MiB) rather than streaming it in pieces, and there are no retries, no
multipart uploads and no server-side encryption negotiation.

## Media handling

### WebM and QuickTime metadata is not parsed

`studio/media.py` reads duration and dimensions from an **unfragmented MP4**:
the `mvhd` box for the duration and the first `tkhd` box that carries non-zero
dimensions. `webm_metadata()` always returns `(0.0, 0, 0)` and says so in its
docstring; QuickTime files get the same treatment. Consequence: a WebM or .mov
asset is stored and served correctly but its `duration_seconds`, `width` and
`height` stay NULL, so the library cannot show a length or a resolution and
cannot lay it out at the right aspect ratio. A zero from these probes means "was
not read", never "is zero". Fragmented MP4 is also unread.

Thumbnails require Pillow, imported lazily inside `thumbnail()`. Without it, the
module still imports and no thumbnail is produced — the asset has a NULL
`thumbnail_key` and the thumbnail route has nothing to serve.

### No in-browser media processing beyond playback and the mask brush

The React client plays video with a plain `<video controls>` element and renders
images with an `<img>`. There is no client-side transcode, crop, trim,
compositing, frame extraction or in-browser metadata probe. The one drawn
surface is the CSS class `.mask-stage canvas` in
`web/src/styles/index.css`; no component in `web/src` currently renders a
`.mask-stage`, and no canvas is created anywhere in the client. Consequence:
inpainting is only reachable by supplying a `mask_asset_id` that already exists
as an asset; the UI cannot paint one.

### Video durations the placeholder can produce

The mock adapter's video encoder only emits three real lengths (0.44 s, 0.88 s,
1.76 s of real frames at its fixed frame delay). It offers those, not a menu of
values a real model might accept.

## Cost and usage

### Cost is never computed for a BYOK provider

The platform is bring-your-own-key: the request is billed by the provider the
workspace connected, and the platform has no invoice to read. `fal.py` quotes
the provider's published pricing prose verbatim inside a cost estimate **whose
amount stays unknown**; it does not compute a number. `usage_records.actual_cost_json`
is nullable and stays NULL when no provider reported a price. Consequence: every
estimate is labelled *estimate*, every unreported actual shows as unknown rather
than 0, and no total in the UI is a bill. Fabricating one would be a fabricated
invoice.

### Progress is only ever what the provider reported

fal reports `IN_QUEUE` and `IN_PROGRESS` and nothing in between, so the adapter
never reports a percentage. Jobs carry `progress_is_estimate` and the client must
label an estimate as approximate. Consequence: a long generation shows an
indeterminate state, not a progress bar crawling toward a number nobody measured.

## Database

### SQLite is the only exercised database; PostgreSQL is documented but not wired

`studio/db.py` opens a SQLite file through `sqlite3`. `STUDIO_DATABASE_URL` is
parsed into `DatabaseConfig.url` and **is not used to connect to anything** —
there is no PostgreSQL driver in the repository and no code path that reads that
value. `docs/SCHEMA.md` records the concrete differences a port has to handle
(TEXT ids vs uuid, INTEGER 0/1 booleans, JSON as TEXT, CHECK instead of enums,
REAL vs double precision, a pool instead of thread-local connections).
Consequence: treat `STUDIO_DATABASE_URL` as reserved, and do not plan a
PostgreSQL deployment from this code.

### SQLite write contention

One writer at a time, with WAL and a busy timeout as mitigation
(risk R7 in `docs/PLAN.md`). Consequence: many concurrent jobs on one file will
serialise their writes, and a long write transaction blocks every other writer
until it commits.

## Rate limiting

### Per-process fixed-window, not distributed

`studio/security/rate_limit.py` counts hits in the `rate_limits` table, one row
per `(bucket, window_start)`, in a 60-second window. Buckets are hashed where
they contain an address. Consequence: the limit is shared by every process that
points at the same SQLite file and by nobody else. Two instances with separate
databases enforce their own limits independently, so the effective ceiling is
(number of instances) x (configured limit). It also counts the request that is
refused, so a client that keeps hammering stays refused rather than getting a
fresh allowance when the window rolls.

## The HTTP layer and the client

### 74 routes are served, from one process

`studio/app.py` is the composition root: it migrates the database, builds the
object store, the repositories, the six services and the router, and hands the
result to `build_server()`. `python -m studio.app` serves the API and, when
`web/dist` exists, the built client from the same origin. `studio/routes/`
registers 74 routes across eight route modules (system, auth, connections,
models, generations, jobs, assets, library), whose paths fall under fourteen API
prefixes -- `system`, `health`, `auth`, `connections`, `models`, `generations`,
`jobs`, `assets`, `projects`, `presets`, `prompts`, `assistant`, `characters` and
`usage`.

Consequence: the whole platform runs as **one process with in-process worker
threads**, not as a separate queue and worker tier. `STUDIO_JOB_WORKERS` sets
the thread count (default 2); `python -m studio.app` starts them, so a queued
job begins immediately. `STUDIO_JOB_WORKERS=0` serves requests and generates
nothing, which is what the test suite uses so a test can drive the runner one
step at a time and observe each state.

Leases are in SQLite, so several processes *can* share one file, but write
contention is then the limit (see above).

### The prompt assistant is rule-based, and says so

`POST /api/prompts/improve` exists and works, but it is not an LLM. It is a
deterministic rewriter in `studio/services/assistant.py` supporting four
operations: `structure`, `expand`, `condense` and `to_video`. Every word it adds
comes from a fixed list of craft vocabulary; every change it makes is named in
`notes`; and the response carries `provider: "local-rules"`, `method:
"deterministic"` and `external_call: false` so no surface can present it as a
model call. `GET /api/assistant/capabilities` describes what it can do.

Nothing is applied to the user's prompt without an explicit click. When a
rewrite comes back identical the response says `changed: false` rather than
pretending otherwise.

What this does **not** do: diagnose a provider error, or rewrite with the
imagination of a language model. For that, the assistant would have to call a
model the workspace connected, and it does not. That is a deliberate choice --
a rules engine that is honest about being one beats a fabricated suggestion.

### Studio tests exist, and cover the vertical slice

`tests/test_studio.py` mounts the real `HttpApp` on a real socket and speaks
HTTP to it: 46 tests covering status, registration and CSRF, the BYOK envelope,
SSRF refusals, the model registry and capability honesty, queueing, the worker
completing a job, asset isolation between workspaces, upload validation, job
history, cancellation and the library. The provider under test is the local
mock adapter, which is the only adapter that may render without a network call.

Beside it, and without a socket: `tests/test_security.py` (130 tests on the
egress policy, envelope sealing and redaction), `tests/test_adapters.py` (110 on
parameter schemas, capability profiles and the mapping interpreter),
`tests/test_library.py` (58 on presets, workspace isolation, provenance, usage
honesty and audit immutability) and `tests/test_contract.py` (26 that compare the
routes the client calls with the routes the server serves). 617 tests in total.

What is **not** covered: any real provider, the S3 backend, PostgreSQL, and the
browser (there is no DOM test; the client is verified by `tsc` and `vite
build`).

## Rates, retries and shutdown

### Retry, cancellation and timeouts are written but unproven end to end

`studio/services/runner.py` implements lease-based claiming, exponential
backoff, cancellation and a provider timeout, and states its three rules in the
docstring. None of it has been driven against a real provider, because doing so
requires spending money. Consequence: the recovery behaviour after a crash
mid-job is designed, not demonstrated.

### `job_temp_files` has no sweeper

The table records which temporary files a job created and when they were
removed. No code in the repository writes to it or sweeps anything. Consequence:
a staging file the declarative adapter wrote under the system temp directory is
not tracked or cleaned up by the platform.

## Smaller gaps worth naming

| gap | consequence |
|---|---|
| `projects.cover_asset_id` has no foreign key to `assets` | nothing stops it pointing at a deleted asset; a cover can be a dangling id |
| `worker_heartbeats` is never written | `/api/system/status` reports whether the runner thread is alive, but not which job each worker holds; the table that would answer that is unused |
| no `api/studio.py` Vercel handler | the studio is a long-lived server, not a serverless function; `api/` still holds only the nine Prompt Layer handlers, and the studio is reached by running `python -m studio.app` |
| CSRF is skipped when no session cookie is present | registration and sign-in have no session to protect, so they rely on the origin check alone; every authenticated state-changing route still requires the token |
| no email delivery | `forgot-password` returns `delivery: "manual"` and hands back a reset token; there is no mailer |
| no workspace invitations | the schema has `workspace_members` and four roles, and registration creates a personal workspace, but no route adds a second member |
| the client tries three undocumented prompt-assistant paths | `/api/prompts/improve`, `/api/prompts/improvements`, `/api/improve` are in `web/src/api/endpoints.ts` and in none of the contract; all three will 404 |
| fal pricing lookup is best-effort | the public pricing listing ignores every filter parameter it is given and paginates 40 records at a time, so a price is found by matching the listing's own `id` field — and may not be found at all |
| no object-store retry or multipart | a large upload that fails half way is a failure, not a resume |
| `.env` is not loaded by Python | `studio/config.py` reads `os.environ` only; a `.env` file does nothing outside Docker Compose unless your shell exports it |
