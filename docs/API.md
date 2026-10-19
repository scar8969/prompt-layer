# Studio HTTP API

This document mirrors `docs/API_CONTRACT.md`. The contract is the source of
truth; where the two disagree, the contract wins and this file is wrong.

## Status of the HTTP layer

**The route table is written and served.**

`studio/app.py` is the composition root. `build_app(config)` loads the
configuration, migrates the database, builds the object store, the
repositories, the six services and the router, and registers every route group.
`build_server(app.http, ...)` wraps that in a threaded HTTP server, and
`python -m studio.app` starts it. When `web/dist` exists it also serves the
built client, so the API and the app share an origin.

| file | state |
|---|---|
| `studio/routes/` (one module per group) | written: system, auth, connections, models, generations, jobs, assets, library |
| `studio/app.py` (config -> db -> services -> router -> server) | written; `--check` validates the configuration without binding a port |
| `studio/services/` | written: connections, generations, assets, runner, assistant, plus the repository layer |
| `api/studio.py` (a Vercel function) | deliberately absent: the studio is a long-lived server with worker threads, not a serverless handler |

74 routes are registered. `GET /api/system/status` reports what the running
process actually has: the database and storage backends, the adapter slugs from
the live registry, the missing configuration keys, the egress policy, and
whether the worker thread is running.

The contract below is what the server implements, and `tests/test_studio.py`
mounts the real app on a real socket to prove it. The pre-existing Prompt Layer
functions under `api/` are a separate, unrelated surface; `GET /api/health`
belongs to them.

The service layer that sits behind those routes is written:
`studio/services/connections.py`, `generations.py`, `assets.py`, `runner.py`
and `studio/repository.py`. The client in `web/src` is written against these
routes and was exercised against them.

## Envelope

Every response is JSON. A success response is an object whose payload lives
under a key named after the resource: `user`, `workspace`, `workspaces`,
`permissions`, `connection`, `connections`, `test`, `types`, `model`,
`models`, `schema`, `job`, `jobs`, `events`, `project`, `projects`,
`asset`, `assets`, `preset`, `presets`, `prompt`, `prompts`, `character`,
`characters`, `usage`, `status`. Simple acknowledgements are
`{"ok": true, ...}`.

Every failure uses HTTP 4xx/5xx and this body, produced by
`ApiError.to_payload` in `studio/errors.py`:

```json
{"error": {"code": "not_found", "message": "...", "hint": "...",
           "details": {}, "field": "...", "retryable": false,
           "request_id": "..."}}
```

`code` and `message` are always present; `hint`, `details`, `field`,
`provider_message`, `retryable` and `request_id` appear when they apply.
`retryable` is true only for `rate_limited`, `timeout`, `provider_error` and
`internal_error`.

### Error codes and their HTTP status

Defined in `studio/errors.py`.

| code | status | meaning |
|---|---|---|
| `auth_failed` | 401 | the provider rejected the credential |
| `endpoint_not_found` | 404 | the base URL answered, but not with the API we asked for |
| `unsupported_operation` | 400 | the provider does not offer this operation |
| `invalid_model_id` | 400 | the provider does not know this model |
| `rate_limited` | 429 | the provider is throttling us |
| `timeout` | 504 | no answer inside the configured budget |
| `invalid_response` | 502 | the answer could not be understood |
| `network_restricted` | 400 | egress policy refused the destination |
| `provider_error` | 502 | the provider reported a failure of its own |
| `insufficient_balance` | 402 | the provider account cannot pay |
| `output_download_failed` | 502 | the artefact could not be fetched or stored |
| `storage_failed` | 500 | the object could not be written |
| `malformed_url` | 400 | the URL did not parse (also used for HTTP 405) |
| `insecure_url` | 400 | http where https is required |
| `validation_failed` | 400 | a field is wrong |
| `unauthenticated` | 401 | no valid session |
| `forbidden` | 403 | the role or origin does not permit this |
| `not_found` | 404 | the row does not exist, or belongs to another workspace |
| `conflict` | 409 | the name is already taken |
| `rate_limit_exceeded` | 429 | this deployment is throttling the caller |
| `capability_unsupported` | 400 | the model cannot do what was asked, refused before any spend |
| `configuration_required` | 503 | an environment variable a feature needs is missing |
| `internal_error` | 500 | a bug; the request id identifies it in the logs |

Anything the client must branch on should match on `code`, never on
`message`.

## Session cookie and CSRF

* The session cookie is `studio_session`: `HttpOnly`, `SameSite=Lax`,
  `Path=/`, and `Secure` when `STUDIO_REQUIRE_HTTPS` is on. It holds the
  session token and nothing else - no user id, no role, no workspace. Every one
  of those is re-read from the database on each request.
* The CSRF token is returned in the register and login response body as
  `session.csrf_token`. It must be sent as the `X-CSRF-Token` header on every
  non-GET request. The token is `HMAC(auth_secret, session_id)`, so it is bound
  to that one session.
* Two independent checks run before any state-changing handler: the `Origin`
  (or, failing that, `Referer`) must be one of this deployment's own origins,
  and the CSRF header must be present and match. A cross-site form post cannot
  set a header, which is why the header is required rather than a body field.
* The client keeps the CSRF token in memory only. It is never written to
  localStorage: `web/src/state/storage.ts` refuses any key matching a secret
  pattern.

## Idempotency of generation POSTs

A generation POST is the one write that can cost money, so it is the one that
is protected against a double click.

* The body carries `client_request_id`. It is stored on
  `generation_jobs.idempotency_key`, which carries a **partial unique index**
  `idx_jobs_idempotency (workspace_id, idempotency_key) WHERE idempotency_key
  IS NOT NULL` (`studio/migrations/0004_jobs.sql`).
* The row is written before the provider is contacted. Submitting the same
  `client_request_id` twice in one workspace cannot create a second job and
  therefore cannot buy a second generation.
* Retry (`POST /api/jobs/{id}/retry`) creates a **new** job that links back to
  its parent through `parent_job_id` / `relation` and starts a fresh attempt
  count. It never resubmits the previous provider job, because that would be a
  second charge for the same work.
* The worker also refuses to submit a job that already has a
  `provider_job_ref`: resuming means resuming the poll
  (`studio/services/runner.py`).
* `idempotency_keys` in `studio/migrations/0006_operations.sql` extends the
  same idea to non-generation writes, keyed by `(key, scope)` with a stored
  response and an expiry.

---

## Routes

Auth column: `yes` means a valid `studio_session` cookie is required; `no`
means the route is reachable signed out. Every non-GET route additionally
requires the CSRF header (see above).

### System

| method | path | auth | body | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/system/status` | no | - | `status` | `internal_error` |

Response `status`:

```
{version, environment, time_utc, database, storage,
 adapters:[{slug, label, summary, is_mock, discovers_models, supports_cancel,
            auth_schemes, default_modes}],
 mock_provider_enabled,
 billing:{mode:"byok", platform_credits:false, note},
 missing_configuration:[...], parameter_types:[...], camera_moves:[...],
 reference_roles:[...], database_backend, storage_backend}
```

`missing_configuration` is the list from
`Configuration.missing_for(...)`, i.e. the environment variable names a
disabled feature needs. It is how the UI says "this part needs configuration"
instead of failing mysteriously.

### Auth

| method | path | auth | body | success key | error codes |
|---|---|---|---|---|---|
| POST | `/api/auth/register` | no | `email`, `password`, `display_name` | `user`, `workspace`, `session{csrf_token}` | `validation_failed`, `conflict`, `rate_limit_exceeded` |
| POST | `/api/auth/login` | no | `email`, `password` | `user`, `workspace`, `session{csrf_token}` | `validation_failed`, `auth_failed`, `unauthenticated`, `forbidden` (locked account), `rate_limit_exceeded` |
| POST | `/api/auth/logout` | yes | - | `ok` | `unauthenticated` |
| GET | `/api/auth/me` | yes | - | `user`, `workspace`, `workspaces[{id,name,slug,role}]`, `permissions[]` | `unauthenticated` |
| POST | `/api/auth/change-password` | yes | `current_password`, `new_password` | `ok` | `validation_failed`, `auth_failed`, `unauthenticated` |
| POST | `/api/auth/forgot-password` | no | `email` | `ok`, `delivery:"manual"` (development also returns `reset_token`) | `validation_failed`, `rate_limit_exceeded` |
| POST | `/api/auth/reset-password` | no | `token`, `new_password` | `ok` | `validation_failed`, `not_found`, `rate_limit_exceeded` |

Registration creates the user, a personal workspace and the owner membership in
one transaction (`studio/security/accounts.py`), so a new account lands
somewhere that already works.

Password policy is a length floor (`STUDIO_PASSWORD_MIN_LENGTH`, default 10)
plus a small list of obviously weak strings. There are deliberately no
composition rules. Ten consecutive failed sign-ins lock the account for 900
seconds.

### Connections (BYOK / BYOM)

| method | path | auth | body | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/connections` | yes | - | `connections[]` | `unauthenticated` |
| GET | `/api/connections/types` | yes | - | `types[]` | `unauthenticated` |
| POST | `/api/connections` | yes (admin) | see below | `connection`, `models[]` | `validation_failed`, `conflict`, `network_restricted`, `malformed_url`, `insecure_url`, `configuration_required`, `forbidden` |
| GET | `/api/connections/{id}` | yes | - | `connection` | `not_found`, `unauthenticated` |
| PATCH | `/api/connections/{id}` | yes (admin) | `name?`, `base_url?`, `is_enabled?`, `extra_headers?`, `config?`, `auth_scheme?`, `auth_header_name?` | `connection` | `validation_failed`, `conflict`, `not_found`, `network_restricted` |
| DELETE | `/api/connections/{id}` | yes (admin) | - | `ok`, `models_disabled`, `jobs_preserved` | `not_found`, `forbidden` |
| POST | `/api/connections/{id}/test` | yes (editor) | - | `test{outcome, ok, http_status, latency_ms, message, detail, models_seen, checked_at}` | `not_found`, `configuration_required`, plus any of the provider codes as the *outcome value* rather than an error |
| POST | `/api/connections/{id}/rotate-key` | yes (admin) | `credential` | `connection` | `validation_failed`, `not_found`, `configuration_required` |
| GET | `/api/connections/{id}/models` | yes | - | `models[]` | `not_found` |
| POST | `/api/connections/{id}/models` | yes (admin) | `ModelInput` | `model` | `validation_failed`, `not_found`, `conflict` |

Create body:
`{name, adapter_type, base_url, credential, auth_scheme, auth_header_name,
extra_headers, config, allowed_hosts, models:[ModelInput]}`

The `connection` shape:

```
{id, name, adapter_type, adapter_label, base_url, auth_scheme, model_count,
 is_enabled, last_test_status, last_test_at, last_used_at, created_at,
 credential:{present, last4, key_id, fingerprint}, extra_header_names:[]}
```

The credential itself never comes back. Only `present`, the last four
characters, the key id and a public fingerprint are returned.

A connection test **stores** its outcome with a timestamp in
`connection_tests`. It is an observation, not a flag. A provider rejection is
reported as a `test.outcome` value (`auth_failed`, `endpoint_not_found`,
`timeout`, ...) rather than as a 4xx, so the wizard can render the specific
screen.

`ModelInput`:
`{model_key, display_name, modality, modes:[string], capabilities:{...},
parameter_schema:{parameters:[ParameterSpec]}, defaults:{}, limits:{}, source}`

`ParameterSpec`:
`{name, type, label, description, default, required, minimum, maximum, step,
options:[], option_labels:{}, item_type, min_items, max_items, max_length,
pattern, prompt_mapped, api_key, group, advanced}`

`Capabilities`:
`{modality, modes, aspect_ratios, resolutions, durations_seconds, frame_rates,
reference_roles, max_reference_images, max_outputs, supports_negative_prompt,
supports_seed, supports_audio, supports_mask, native_camera_moves,
restrictions, notes, max_input_bytes}`

Four adapters are registered in the process, which is exactly the set the
`api_connections.adapter_type` CHECK constraint admits:

```
known_adapters() == ('declarative_http', 'fal_queue', 'mock', 'openai_images')
```

`openai_images` is a real adapter class (`studio/adapters/openai.py`) that
installs the declarative mapping presets per mode. Of the four, only `mock`
answers without a network call, and every surface that shows its output is
required to carry `is_mock: true`. The other three have never been run against
a live provider; see `docs/LIMITATIONS.md`.

### Models

| method | path | auth | body / query | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/models` | yes | query: `modality`, `source`, `favorite`, `enabled`, `q` | `models[]` | `unauthenticated` |
| GET | `/api/models/{id}` | yes | - | `model` | `not_found` |
| GET | `/api/models/{id}/capabilities` | yes | - | `capabilities`, `modes[{key,label,supported}]`, `camera_moves[{key,label,phrase,is_native}]`, `reference_roles[]` | `not_found` |
| GET | `/api/models/{id}/parameters` | yes | - | `schema{parameters[]}`, `parameter_types[]` | `not_found` |
| PATCH | `/api/models/{id}` | yes (admin) | `display_name?`, `is_enabled?`, `is_favorite?`, `is_default?`, `modes?`, `capabilities?`, `parameter_schema?`, `defaults?` | `model` | `validation_failed`, `not_found`, `capability_unsupported` |
| DELETE | `/api/models/{id}` | yes (admin) | - | `ok` | `not_found` |

The `model` shape:

```
{id, connection_id, workspace_id, model_key, display_name, modality, modes,
 capabilities, parameter_schema, defaults, limits, source, is_enabled,
 is_favorite, is_default, created_at, updated_at,
 provider:{connection_name, adapter_type, adapter_label, base_url, is_mock,
           connection_enabled}}
```

Modes are validated on write against the modality, so an image model cannot
claim to extend a video.

### Generations

These routes queue work. They do not contact the provider; a worker does that.

| method | path | auth | body | success key | error codes |
|---|---|---|---|---|---|
| POST | `/api/generations/images` | yes (editor) | see below | `job` | `validation_failed`, `capability_unsupported`, `not_found`, `configuration_required`, `forbidden`, `rate_limit_exceeded` |
| POST | `/api/generations/videos` | yes (editor) | image body + video fields | `job` | same |
| POST | `/api/generations/{job_id}/variations` | yes (editor) | `count`, `prompt?`, `seed?` | `job` | `not_found`, `capability_unsupported`, `validation_failed` |
| POST | `/api/generations/{job_id}/edit` | yes (editor) | `mode:"inpaint"\|"outpaint"`, `prompt`, `mask_asset_id`, `expand:{left,right,top,bottom}`, `parameters` | `job` | `not_found`, `capability_unsupported`, `validation_failed` |
| POST | `/api/generations/{job_id}/extend` | yes (editor) | `seconds`, `prompt` | `job` | `not_found`, `capability_unsupported`, `validation_failed` |
| POST | `/api/generations/{job_id}/transform` | yes (editor) | `mode`, `prompt`, `parameters` | `job` | `not_found`, `capability_unsupported`, `validation_failed` |

Image body:
`{model_id | (connection_id, model_key), mode, prompt, negative_prompt,
parameters, references:[{asset_id | url, role}], mask_asset_id, count, seed,
project_id, preset_id, client_request_id}`

Video body adds:
`{duration_seconds, aspect_ratio, resolution, camera_move, generate_audio,
first_frame_asset_id, last_frame_asset_id}`

The model is either named by id or resolved from a connection plus a model key.
The service resolves the model, its capability profile, validates the request
against that profile, and writes a queued row
(`studio/services/generations.py`). A request the model cannot serve is refused
**before** any spend, with `capability_unsupported`.

### Jobs

| method | path | auth | body / query | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/jobs` | yes | query: `state`, `modality`, `project_id`, `model_id`, `limit`, `cursor` | `jobs[]`, `next_cursor` | `unauthenticated` |
| GET | `/api/jobs/{id}` | yes | - | `job` | `not_found` |
| GET | `/api/jobs/{id}/events` | yes | - | `events[{id, kind, state, message, detail, created_at}]` | `not_found` |
| POST | `/api/jobs/{id}/cancel` | yes (editor) | - | `job` | `not_found`, `unsupported_operation` (the provider cannot cancel), `conflict` (already terminal) |
| POST | `/api/jobs/{id}/retry` | yes (editor) | - | `job` (a new job) | `not_found`, `conflict`, `validation_failed` |

The `job` shape:

```
{id, workspace_id, project_id, model_id,
 model:{model_key, display_name, modality,
        provider:{connection_name, adapter_label, is_mock}},
 mode, kind, state, prompt, negative_prompt, parameters, references, seed,
 count, provider_job_id, provider_status, progress, progress_is_estimate,
 message, error:{code,message,hint,details}?, attempt, max_attempts, is_mock,
 cost_estimate, outputs:[Output], created_at, started_at, finished_at,
 elapsed_ms, parent_job_id, relation}
```

`Output`:
`{id, asset_id, url, content_type, width, height, duration_seconds, file_name,
byte_size, seed, metadata, is_mock}`

Job states, from the `generation_jobs.status` CHECK constraint: `draft`,
`queued`, `validating`, `submitting`, `processing`, `downloading`,
`completed`, `failed`, `cancelled`, `timed_out`. The worker only acts on
`queued`, `validating`, `submitting`, `processing`, `downloading`.

### Projects and assets

| method | path | auth | body / query | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/projects` | yes | - | `projects[]` | `unauthenticated` |
| POST | `/api/projects` | yes (editor) | `name`, `description` | `project` | `validation_failed` |
| GET | `/api/projects/{id}` | yes | - | `project`, `assets[]` | `not_found` |
| PATCH | `/api/projects/{id}` | yes (editor) | `name?`, `description?`, `is_favorite?`, `is_archived?` | `project` | `validation_failed`, `not_found` |
| DELETE | `/api/projects/{id}` | yes (editor) | - | `ok`, `assets_unassigned` | `not_found` |
| POST | `/api/projects/{id}/assets` | yes (editor) | `asset_ids:[]` | `project` | `validation_failed`, `not_found` |
| GET | `/api/assets` | yes | query: `project_id`, `kind`, `favorite`, `q`, `limit`, `cursor` | `assets[]`, `next_cursor` | `unauthenticated` |
| POST | `/api/assets` | yes (editor) | `file_name`, `content_type`, `data_base64`, `project_id?`, `kind?` | `asset` | `validation_failed` (including above the configured ceiling), `storage_failed` |
| GET | `/api/assets/{id}` | yes | - | `asset` | `not_found` |
| PATCH | `/api/assets/{id}` | yes (editor) | `name?`, `project_id?`, `is_favorite?` | `asset` | `validation_failed`, `not_found` |
| DELETE | `/api/assets/{id}` | yes (editor) | - | `ok` | `not_found` |
| GET | `/api/assets/{id}/content` | yes | - | raw bytes, `Content-Type` from the stored signature | `not_found`, `storage_failed` |
| GET | `/api/assets/{id}/thumbnail` | yes | - | raw bytes | `not_found`, `capability_unsupported` when no thumbnail exists |

`Asset`:
`{id, workspace_id, project_id, job_id, kind:"image"|"video"|"upload"|"mask",
name, content_type, byte_size, width, height, duration_seconds, is_favorite,
is_mock, content_url, thumbnail_url, created_at, source:"generated"|"uploaded"}`

Uploads are judged by their bytes, never by the declared type: a Windows
executable renamed to `.png` is refused twice over. The effective ceiling is
`STUDIO_MAX_UPLOAD_BYTES` (default 25 MiB); a larger body is rejected with
`validation_failed`. The transport also refuses any body over 48 MiB before it
is parsed.

### Library

| method | path | auth | body / query | success key | error codes |
|---|---|---|---|---|---|
| GET | `/api/presets` | yes | query: `modality`, `category`, `q` | `presets[]` | `unauthenticated` |
| POST | `/api/presets` | yes (editor) | `name`, `description`, `modality`, `category`, `prompt_template`, `negative_prompt_template`, `aspect_ratio`, `model_preference`, `parameters`, `camera`, `notes` | `preset` | `validation_failed`, `conflict` |
| GET | `/api/presets/{id}` | yes | - | `preset` | `not_found` |
| PATCH | `/api/presets/{id}` | yes (editor) | partial preset | `preset` | `validation_failed`, `not_found` |
| DELETE | `/api/presets/{id}` | yes (editor) | - | `ok` | `not_found`, `forbidden` (built-in) |
| POST | `/api/presets/{id}/favorite` | yes | `favorite` | `preset` | `validation_failed`, `not_found` |
| GET | `/api/prompts` | yes | query: `q`, `model_id` | `prompts[]` | `unauthenticated` |
| POST | `/api/prompts` | yes (editor) | `text`, `title?`, `negative_prompt?`, `model_id?`, `tags?`, `variables?` | `prompt` | `validation_failed` |
| PATCH | `/api/prompts/{id}` | yes (editor) | partial prompt | `prompt` | `validation_failed`, `not_found` |
| DELETE | `/api/prompts/{id}` | yes (editor) | - | `ok` | `not_found` |
| GET | `/api/characters` | yes | - | `characters[]` | `unauthenticated` |
| POST | `/api/characters` | yes (editor) | `name`, `description`, `attributes`, `default_prompt`, `preferred_model_id`, `tags`, `reference_asset_ids` | `character` | `validation_failed` |
| GET | `/api/characters/{id}` | yes | - | `character`, `references[]` | `not_found` |
| PATCH | `/api/characters/{id}` | yes (editor) | partial character | `character` | `validation_failed`, `not_found` |
| DELETE | `/api/characters/{id}` | yes (editor) | - | `ok` | `not_found` |
| POST | `/api/characters/{id}/references` | yes (editor) | `asset_id`, `role` | `character` | `validation_failed`, `not_found` |
| GET | `/api/usage` | yes | query: `limit` | `usage[]`, `billing{mode:"byok", platform_credits:false, note}` | `unauthenticated` |

`usage[]` entries:
`{id, job_id, kind, model_id, provider, quantity, unit, is_estimate,
is_billable, note, created_at}`

A usage row whose provider reported no price keeps `actual_cost` NULL rather
than showing a fabricated zero.

Note that the client also tries three undocumented prompt-assistant paths -
`/api/prompts/improve`, `/api/prompts/improvements`, `/api/improve`
(`web/src/api/endpoints.ts`). None of them appears in the contract, and none is
implemented in Studio.

---

## Honesty rules the client must respect

Copied from `docs/API_CONTRACT.md`; these are contract, not style advice.

1. If `model.provider.is_mock` or `job.is_mock` is true, the UI shows a
   test-mode badge on the model, the job and the output. Never present mock
   output as a real generation.
2. `cost_estimate.is_estimate` is always shown with the word *estimate*. When
   `is_billable` is false the UI says the request is billed by the user's own
   provider.
3. A job in state `queued` or `processing` shows no percentage unless
   `progress_is_estimate` is true, in which case it is labelled approximate.
4. Modes absent from `model.modes` are disabled with the reason from
   `capabilities`, never hidden silently and never enabled optimistically.
5. A camera move whose `is_native` is false is labelled "prompt wording" and
   never presented as a provider parameter.
