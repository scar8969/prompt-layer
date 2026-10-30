# Implementation plan and risk register

This repository holds one product: a BYOK image and video studio whose differentiator
is that it tells you what each provider does to your prompt. The static preflight page
that used to sit beside it is gone — its dataset and its assembly logic are served by
`studio/routes/prompt.py` and rendered by the Prompt Lab screen, inside the same
session and the same navigation as generation. Nothing is left at the root except the
application itself (`tests/test_deploy.py` asserts both).

## Stack decision

Studio is a Python package at studio/, served as a long-lived process by
`python -m studio.app`, with a React 19 + TypeScript + Vite client built into
web/dist and served from the same origin. This reuses the pattern the repository
already proves: `BaseHTTPRequestHandler` under the hood, a dependency-light package
next to it, and pytest tests that speak real HTTP to a real server.

Locally that is the only way to run it: one process with in-process worker threads,
because the queue needs durable leases and long-running provider polling.

On Vercel the same application is mounted as one catch-all function, `api/studio.py`,
which serves `/api/*` from the router and every other path from `web/dist`. The tick
(`api/studio_cron.py`) drives jobs that outlive a request. The older
`api/preflight.py`, `api/compile.py` and friends remain as JSON endpoints over the
same `promptlayer/` package; they are not a second product, and nothing in the client
calls them.

The alternative -- Postgres, Redis, BullMQ, S3, a Node service -- was rejected because
none of those services are available in this environment (no pnpm, no Docker, no
psql), and the master prompt forbids replacing working infrastructure without a clear
reason. SQLite in WAL mode supplies durable relational storage, real transactions and
a real migration runner with no install step. Object storage writes to disk with an
S3-compatible backend behind the same interface. The job queue is a table with leases,
which is a durable queue in the only sense that matters here: a job survives a restart.
See docs/SCHEMA.md and docs/LIMITATIONS.md for the portability notes that come with
that choice.

## Phases

1. Foundation -- done. config, errors, redaction, logging, crypto, database, seven
   migrations, SSRF-guarded egress client, storage, media signature sniffing.
2. Model and API infrastructure -- done. Four adapters (`fal_queue`,
   `openai_images`, `declarative_http`, `mock`) over a shared base, the sealed
   credential service, and the model registry with declared capability profiles.
3. Services -- done. Repository layer, connections, generations, assets, the
   lease-based runner, presets, prompts, characters and usage records.
4. HTTP -- done. 74 routes across eight groups, sessions with CSRF, and the
   composition root in `studio/app.py`. Served by `python -m studio.app` rather
   than by a Vercel function (see the stack decision above).
5. Client -- done. React 19 application with eleven navigation surfaces, building
   to `web/dist`, verified by `tsc --noEmit` and `vite build`.
6. Reliability -- done. 46 HTTP tests in `tests/test_studio.py`, 324 unit and
   repository tests beside it, 617 in the whole suite, documentation,
   `.env.example`, a Dockerfile and a compose file.

## Risks

R1 Credential exposure. Mitigated by envelope encryption with a key held outside the
   database, a single code path that opens a credential, redaction filters on every log
   record, and a test that greps responses for the submitted key.
R2 SSRF through a user-supplied base URL. Mitigated by the egress policy in studio/net.py:
   scheme allowlist, private and metadata address blocks, DNS pinning, per-hop redirect
   validation, response caps, timeouts. Permissive egress is opt-in and logged.
R3 Fake success. Mitigated by construction: no adapter returns a status a provider did
   not send, mock output is labelled at the model, job and asset level, and estimates
   are labelled estimates.
R4 Duplicate billing on retry. Mitigated by carrying the client_request_id through the
   idempotency table and by making retry create a new job that links to its parent
   rather than resubmitting the old provider job.
R5 Cross-tenant data access. Mitigated by workspace_id on every row and a service layer
   that takes workspace_id as a required argument, so an endpoint cannot forget it.
R6 Provider drift. Mitigated by keeping the wire format inside one adapter per protocol
   and by tests that run every adapter against a local mock provider over real HTTP.
R7 SQLite write contention under concurrent jobs. Mitigated by WAL, a busy timeout, and
   short write transactions; documented as a scaling limit rather than hidden.
