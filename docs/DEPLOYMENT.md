# Deployment

There are two shapes, and only one of them is wired to anything that runs
today. Read the state table before choosing.

| shape | artifact | what it actually deploys | state |
|---|---|---|---|
| (a) Vercel Python functions | `vercel.json` | the nine functions in `api/` plus `index.html`. **Not Studio.** | configured and test-asserted |
| (b) single container | `Dockerfile` | the repository, started with `python dev.py` | written for this document; no image has been built from it |

**Studio has no deployment of its own yet.** `docs/PLAN.md` names
`api/studio.py` as its Vercel function and `studio/app.py` as its wiring; neither
file exists. `vercel.json` does not mention either, and `tests/test_deploy.py`
asserts that every file in `api/` is covered by a pattern — so the moment
`api/studio.py` is added, that test fails until `vercel.json` is updated. That
is deliberate: a new endpoint cannot quietly fall outside the configured
patterns.

## What both shapes need from the environment

| variable | why it cannot be omitted in production |
|---|---|
| `STUDIO_ENV=production` | turns off the development defaults: mock provider, plain-http endpoints, loopback egress, `Secure=false` cookies |
| `STUDIO_ENCRYPTION_KEY` | seals provider API keys. Generated on first boot if absent, into the data directory - which is fine on a persistent volume and wrong on an ephemeral one |
| `STUDIO_AUTH_SECRET` | HMAC key for session digests, CSRF tokens and IP hashing. Same rule |
| `STUDIO_PUBLIC_APP_URL` / `STUDIO_API_BASE_URL` | the CSRF origin allowlist. Wrong values mean every state-changing request is answered `forbidden` |
| `STUDIO_REQUIRE_HTTPS=true` | refuses a session cookie that did not arrive over https, and adds HSTS |
| `STUDIO_ALLOW_PRIVATE_HOSTS` | leave empty in production unless you are deliberately reaching a self-hosted inference server |
| `STUDIO_ALLOW_INSECURE_ENDPOINTS=false` | an API key sent over http is an API key handed to the network |
| `STUDIO_ENABLE_MOCK_PROVIDER=false` | with it on, a user can generate a placeholder and believe it is real; it defaults to false in production but set it explicitly |
| `STUDIO_ENCRYPTION_KEY` **backup** | see the security checklist - losing it loses every stored credential |

Full list with defaults: `.env.example`.

## (a) Vercel Python functions

Already configured. `vercel.json`:

* `"framework": null` — pins the project to "Other". Without it, the root
  `requirements.txt` makes Vercel detect a single Python application, go looking
  for one WSGI/ASGI entrypoint and fail the build. `tests/test_deploy.py`
  asserts this.
* `functions`: `api/compile.py` first at 512 MB / 60 s, then `api/*.py` at
  512 MB / 15 s. The specific pattern is listed **before** the wildcard because
  a pattern another pattern already matched is a build error.
* `excludeFiles` keeps `tests/**`, `scripts/**`, `promptlayer/vendor/**`,
  `docs/**` and `requirements-dev.txt` out of the function bundle. The vendored
  fal schemas are build-time input, not runtime input.
* `headers`: `/api/(.*)` is `Cache-Control: no-store`; `/index.html` must
  revalidate.
* `.vercelignore` additionally keeps `dev.py`, the test suite, the parsers and
  the screenshots from being published as static files — Vercel serves the
  project directory as-is, so without it the whole repo root is downloadable.

### Where state lives on Vercel

Not on the platform. Studio's function talks to a Postgres database
(`STUDIO_DATABASE_URL`) and an S3-compatible object store
(`STUDIO_STORAGE_BACKEND=s3`), both configured as environment variables.

The filesystem the function runs on is read-only apart from `/tmp`, and `/tmp`
is per-instance and discarded, so nothing durable is written to it. The
encryption key and the auth secret come from the environment too
(`STUDIO_ENCRYPTION_KEY`, `STUDIO_AUTH_SECRET`); the file-backed fallback that
`studio/config.py` writes when they are unset is for local runs and does not
apply to a deployment.

### What the build actually ships

Two layers ship files, and they are configured separately:

| Layer | What it is | Configured by |
|---|---|---|
| the static layer | everything the domain serves at the root | `.vercelignore` |
| each function's bundle | the code and dependencies one function runs | that function's `excludeFiles` in `vercel.json` |

A file excluded from one and not the other still ships. `scripts/check_built_output.py`
reads the built output — not the configuration — and reports what would actually
be published:

```
$ vercel build --prod --yes
$ python scripts/check_built_output.py
OK       studio.func       files= 1066
OK       studio_cron.func  files= 1066
OK       static             files=  152

the built output publishes nothing it should not
```

Three things this check found that no local test could see, because locally the
whole working tree is present:

1. **`var/` was published.** It holds `studio.db`, `.studio-encryption-key` and
   `.studio-auth-secret`. The encryption key opens every sealed provider
   credential in the database, so this was a disclosure, not a nuisance.
2. **The function bundle had no `psycopg` and no `cryptography`.** The platform
   prefers `uv.lock` over `requirements.txt` when both exist, and the lock
   declared no dependencies — so the bundle shipped `pip` and nothing else. The
   fix was to declare the three packages in `pyproject.toml`, which is what the
   lock is built from. `requirements.txt` stays empty on purpose: it is what
   stops the platform detecting a framework preset.
3. **`web/dist` was excluded from the function that serves it.** `api/studio.py`
   serves the built client out of `web/dist`, and its `excludeFiles` listed
   `web/dist/**`. Every page request would have 404'd on the deployment while
   working perfectly on the machine that built it.

`tests/test_deploy.py` now pins all three, so a new function or a changed
ignore file cannot reintroduce them.

### Health check

`GET /api/health` -> `200 {"success": true, "status": "ok", "models": 20}`.

Studio's own status route is `GET /api/studio/api/system/status`, and it
reports what the running instance actually has: the database and storage
backends, the adapter slugs from the live registry, the missing configuration
keys, the egress policy, and whether the worker is running.

### Logs

Vercel collects stdout/stderr. `api/health.py` and its siblings silence
`log_message` so the platform's own access log is the only per-request line.
Studio's `studio/logging_setup.py` emits one JSON object per line when
`log_json` is set (the production default) with `ts`, `level`, `logger`,
`message` and, when present, `request_id`, `job_id`, `user_id`,
`workspace_id`, `connection_id`, `route`. Every string passes through
`studio.redact.scrub` before it is written.

### What breaks on a read-only filesystem

Everything in Studio that writes. Concretely: `studio/config.py` calls
`data_dir.mkdir(parents=True, exist_ok=True)` at import time, so a read-only
filesystem raises at startup; `Database.migrate()` creates the database file;
`LocalObjectStore` creates the storage root and writes every object;
`_read_or_create_key` writes the two key files when the secrets are unset. The
functions in `api/` are unaffected because they write nothing.

## (b) Single container

`Dockerfile` builds `python:3.14-slim`, installs `requirements.txt` and
`cryptography`, runs as an unprivileged user, declares `/data` as a volume and
starts the service with `python dev.py` — the only runnable entry point in this
repository. It exposes port 8090, which is `dev.py`'s default.

`docker-compose.yml` runs one service, mounts a named volume at `/data`, reads
`.env` through `env_file`, and carries the same health check.

### Required environment in the container

Set at minimum, in `.env` beside `docker-compose.yml`:

```
STUDIO_ENV=production
STUDIO_ENCRYPTION_KEY=<64 hex chars>
STUDIO_AUTH_SECRET=<64 hex chars>
STUDIO_PUBLIC_APP_URL=https://your.host
STUDIO_API_BASE_URL=https://your.host/api
STUDIO_REQUIRE_HTTPS=true
STUDIO_ALLOW_INSECURE_ENDPOINTS=false
STUDIO_ENABLE_MOCK_PROVIDER=false
STUDIO_DATA_DIR=/data
STUDIO_DATABASE_PATH=/data/studio.db
STUDIO_STORAGE_BACKEND=local
STUDIO_STORAGE_ROOT=/data/objects
```

### Where the SQLite file and object storage must live

Under `/data`, which is the volume:

```
/data/studio.db          the database (plus studio.db-wal and studio.db-shm in WAL mode)
/data/objects/           every stored asset when STUDIO_STORAGE_BACKEND=local
/data/.studio-encryption-key   only if STUDIO_ENCRYPTION_KEY is unset
/data/.studio-auth-secret      only if STUDIO_AUTH_SECRET is unset
```

### Why a persistent volume is mandatory

SQLite keeps the database in one file, and WAL mode keeps a `-wal` and `-shm`
beside it. Inside a container that is not on a volume, all three live in the
container's writable layer, which is destroyed when the container is replaced.
So a redeploy or a crash-restart silently takes with it: every user, every
connection, every sealed credential, every job and its history, and every stored
byte. There is no external service holding a copy — that is the trade the SQLite
choice makes (`docs/PLAN.md`, risk R7).

The two key files matter just as much: if the encryption key file is lost with
the container, the rows in the *restored* database are ciphertext nobody can
open. Set `STUDIO_ENCRYPTION_KEY` in the environment, or keep the key file on
the volume and back both up.

### Health check

The container's `HEALTHCHECK` calls `GET /api/health` with Python's own
`urllib`, so no extra binary is needed in a slim image:

```
python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=3).status == 200 else 1)"
```

Expected body: `{"success": true, "status": "ok", "models": 20}`.

### Logs

`dev.py` writes to stdout/stderr, which is what a container runtime collects.
Access lines are prefixed `dev:`. Set `STUDIO_LOG_JSON=true` for one JSON
object per line. Nothing in the repository writes a log file, so no log volume is
needed and none should be added.

### What breaks on a read-only filesystem

Same list as the Vercel section, and one more: `dev.py` reads `index.html`
from the repository root, so the image must keep that file readable — it does.

If you mount `/data` read-only, the container starts (config only *creates* the
data dir, which already exists on a mounted volume) and then fails on the first
write: migrations, uploads, job rows, object writes. `GET /api/health` still
answers 200 because it touches nothing on disk, so a health check alone will not
tell you the volume is read-only. Watch for `storage_failed` and for a
`sqlite3.OperationalError` in the log.

## Rollback

There is no versioned migration down-path and no automated rollback. What you
have:

1. **Before deploying**, copy the volume's database and key files:
   ```bash
   docker run --rm -v studio_data:/data -v "$PWD:/backup" alpine \
     tar czf /backup/data-$(date +%Y%m%dT%H%M%S).tar.gz -C /data .
   ```
   WAL mode means a plain file copy of a live database can be inconsistent; stop
   the service, or run `PRAGMA wal_checkpoint(TRUNCATE)`
   (`Database.checkpoint()`) first.
2. **To roll back the code**, redeploy the previous image tag. The schema is
   forward-only: `Database.migrate()` records the sha256 of every applied file
   and refuses to run if an applied migration has been edited. So an older image
   running against a newer database will start, but it will not undo anything a
   later migration added — restore the pre-deploy backup if the schema changed.
3. **To roll back the data**, stop the container, restore the tarball into the
   volume, start it again. The key files must be restored with it, or every
   credential is unreadable.
4. **On Vercel**, use the dashboard's instant rollback to the previous
   deployment. Nothing else moves, because nothing is persisted.

## Security checklist

- [ ] **Secrets outside the image.** `STUDIO_ENCRYPTION_KEY` and
      `STUDIO_AUTH_SECRET` come from the environment or a mounted file, never
      from a `COPY` in the `Dockerfile` and never from `.env` committed to the
      repository. `.gitignore` already excludes `.env*`.
- [ ] **HTTPS only.** `STUDIO_REQUIRE_HTTPS=true`, termination in front of the
      service, and `STUDIO_ALLOW_INSECURE_ENDPOINTS=false` so no provider call
      goes out in cleartext.
- [ ] **No secrets in logs.** `studio/logging_setup.py` scrubs every message and
      every extra field through `studio/redact.py`; `ApiError` never carries the
      request body or the submitted credential, and a provider's own words go in
      `provider_message` only after the same scrub. Keep it that way: do not add
      a log call that formats a payload.
- [ ] **Back up the encrypted-credential key.** The encryption key is not in the
      database, on purpose — a database dump alone yields nothing replayable.
      The consequence is that losing the key loses every stored provider
      credential. Store it in a secret manager, and record its public key id
      (`credential_key_id`, a 16-hex-char fingerprint) so you can tell which
      rows are under which key during a rotation.
- [ ] **Restrict the data directory** to the service user (the image uses a
      non-root user; `_read_or_create_key` chmods to 0600 where the platform
      allows it).
- [ ] **Leave `STUDIO_ALLOW_PRIVATE_HOSTS` empty** in production. It is the
      documented door to loopback, private and metadata address space, and the
      SSRF guard in `studio/net.py` is the only thing between a user-supplied
      base URL and `169.254.169.254`.
- [ ] **Turn the mock provider off** (`STUDIO_ENABLE_MOCK_PROVIDER=false`).
- [ ] **Proxy trust.** `STUDIO_TRUST_PROXY_HEADERS=true` is only correct behind
      a proxy that sets `X-Forwarded-For` itself. Directly exposed, a client can
      choose its own rate-limit bucket by sending that header.
