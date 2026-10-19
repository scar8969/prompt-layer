# Setup from a clean checkout

Two things live in this repository and they are not the same product.

| what | entry point | state |
|---|---|---|
| **Prompt Layer** - the preflight / improve tool, `promptlayer/`, `api/` | `python dev.py` | complete, runnable, 247 of the suite's 617 tests |
| **Studio** - the BYOK image and video platform, `studio/`, `web/` | `python -m studio.app` | complete and runnable; 74 routes, 46 HTTP tests plus 324 unit and repository tests, client builds to `web/dist` |

This document covers both, and says plainly which steps work today.

## What you need

| requirement | version checked in this repository | why |
|---|---|---|
| Python | 3.14 (`python --version` -> `Python 3.14.3`) | `pyproject.toml` declares `requires-python = "~=3.14.0"` |
| pip | ships with Python | to install the one non-stdlib package Studio needs |
| Node.js + npm | Node v24, npm 11 | only to build the React client in `web/`. Prompt Layer does not need it. |
| Docker | **not required, and not used** | neither product needs it locally |

`pnpm` is not installed here and nothing in the repository needs it.

## 1. Get the code

```bash
git clone <repo-url> prompt-layer
cd prompt-layer
```

## 2. Virtual environment

Optional, and the same on Windows and Linux.

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Everything below works without a venv if you are happy installing into the
system interpreter.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` is **empty on purpose**: Prompt Layer, its dataset and its
Vercel functions are standard library only, and the file exists because
Vercel's Python builder looks for it.

Studio needs exactly one third-party package, `cryptography`, for AES-256-GCM and
scrypt (`studio/crypto.py`). Without it, Studio still imports and runs, but
refuses to store provider credentials — it raises `EncryptionUnavailable`
(HTTP 503, `internal_error`) rather than quietly degrading to something weaker.
Install it:

```bash
pip install cryptography
```

The version present in the environment this was checked against:
`cryptography 49.0.0`.

For the test suite:

```bash
pip install -r requirements-dev.txt
```

which installs `pytest>=8` only.

## 4. Set the two required secrets

```bash
cp .env.example .env       # then edit .env
```

**Important:** nothing in the repository loads a `.env` file.
`studio/config.py` reads `os.environ` only. A `.env` is read by Docker Compose
(`env_file:` in `docker-compose.yml`) and by whatever shell or process manager
you use — not by Python. For local work, export the values:

Linux / macOS:

```bash
export STUDIO_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export STUDIO_AUTH_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
```

Windows PowerShell:

```powershell
$env:STUDIO_ENCRYPTION_KEY = python -c "import secrets; print(secrets.token_hex(32))"
$env:STUDIO_AUTH_SECRET    = python -c "import secrets; print(secrets.token_hex(32))"
```

What happens if you skip this: `studio/config.py` generates each key once and
writes it to `<STUDIO_DATA_DIR>/.studio-encryption-key` and
`.studio-auth-secret` with mode 0600, and sets
`Configuration.generated_keys = true`. The install works, but every stored
credential and every live session becomes unreadable if that directory is lost,
and `missing_for("credentials")` reports nothing missing — the gap is silent
until the file is gone. Set them explicitly anywhere that matters.

`STUDIO_ENCRYPTION_KEY` accepts 64 hex characters, 44 base64 characters, or any
other string (stretched with scrypt). `STUDIO_AUTH_SECRET` is the HMAC key for
session-token digests, CSRF tokens and IP hashing.

## 5. Create the database

There is no CLI and no `manage.py`. The migration runner is
`Database.migrate()` in `studio/db.py`, and this one-liner is the entry point
(verified against this checkout - it prints
`['0001', '0002', '0003', '0004', '0005', '0006']`):

Linux / macOS:

```bash
python -c "from studio.config import load_config; from studio.db import Database; c=load_config(); d=Database(c.database_path, c.database.busy_timeout_ms); print(d.migrate())"
```

Windows PowerShell (same command, one line):

```powershell
python -c "from studio.config import load_config; from studio.db import Database; c=load_config(); d=Database(c.database_path, c.database.busy_timeout_ms); print(d.migrate())"
```

It creates the SQLite file at `STUDIO_DATABASE_PATH` (default
`<STUDIO_DATA_DIR>/studio.db`, default data dir `./var/studio`), applies the
six migrations in order, and records them in `schema_migrations`. Re-running it
is a no-op. If you edit a migration that has already been applied, it refuses
with `MigrationError` and tells you to add a new file instead.

Resulting database: 26 tables (the six migrations plus `schema_migrations`).

## 6. Start something

### Prompt Layer (works today)

```bash
python dev.py
```

It prints the port and one line per mounted function:

```
http://127.0.0.1:8090
  functions: /api/compile -> :... , /api/health -> :... , ...
```

`PORT` overrides 8090. `dev.py` mounts each file in `api/` on its own socket,
exactly as Vercel does, and proxies `/api/<name>` to it. Verified:

```
GET http://127.0.0.1:8090/api/health
{"success": true, "status": "ok", "models": 20}
```

### Studio

The Studio server is one process. It migrates the database on first start, then
serves the API and — if `web/dist` exists — the built client from the same
origin.

```bash
pip install cryptography          # the only third-party dependency
python -m studio.app --port 8091
```

It needs two secrets. Either export them, or let it generate key files under
`var/studio/` on first run (which it does in development only):

```bash
$env:STUDIO_ENCRYPTION_KEY = "<64 hex characters>"   # opens sealed API keys
$env:STUDIO_AUTH_SECRET    = "<32+ characters>"      # signs session tokens
```

Validate the configuration without binding a port:

```bash
python -m studio.app --check
```

That prints every missing configuration key by name and exits non-zero if a
required one is absent.

### The React client

```bash
cd web
npm ci
npm run build      # writes web/dist, which the server then serves
npm run typecheck  # tsc --noEmit
npm run dev        # Vite on 5173, proxying /api to 127.0.0.1:8091
```

For development, run the server on 8091 and Vite alongside it; the proxy in
`web/vite.config.ts` forwards `/api` and keeps cookies on one origin. For a
single-process deployment, run `npm run build` once and let `python -m
studio.app` serve `web/dist`.

## 7. Run the test suite

```bash
python -m pytest -q
```

Verified on this checkout: **617 tests, all passing** (about a minute; the
studio tests each migrate a fresh database, which is most of the wall time).

| file | tests | what it covers |
|---|---|---|
| `tests/test_api.py` | 44 | the nine Vercel functions in `api/`, mounted on real sockets and spoken to over real HTTP |
| `tests/test_deploy.py` | 6 | the deployment shape asserted from `vercel.json` / `.vercelignore` / `.gitignore` |
| `tests/test_assembly.py` | 25 | Prompt Layer request assembly |
| `tests/test_improve.py` | 25 | Prompt Layer improve flow |
| `tests/test_presets.py` | 23 | Prompt Layer preset catalogue |
| `tests/test_reference.py` | 25 | Prompt Layer reference handling |
| `tests/test_storyboard.py` | 47 | Prompt Layer storyboard parsing |
| `tests/test_video.py` | 17 | Prompt Layer video prompts |
| `tests/test_ui.py` | 8 | Prompt Layer page markup |
| `tests/test_fal_dataset.py` | 27 | the bundled fal parameter dataset |
| `tests/test_studio.py` | 46 | the Studio app over real HTTP, with the local mock provider |
| `tests/test_security.py` | 130 | SSRF and egress rules, envelope sealing, redaction |
| `tests/test_adapters.py` | 110 | parameter schemas, capability profiles, mapping interpreter, registry |
| `tests/test_library.py` | 58 | presets, workspace isolation, provenance, usage honesty, audit immutability |
| `tests/test_contract.py` | 26 | the routes the client calls and the routes the server serves |

`tests/test_studio.py` builds a real app (fresh SQLite file, local object
store, worker threads off), mounts it on a real socket, and drives it with a
browser-shaped client. It asserts: registration and CSRF, the sealed-key
envelope never leaving the server, SSRF refusals for metadata addresses,
capability honesty on a registered model, a queued job completing through the
runner, asset isolation between workspaces, upload type sniffing, job history,
cancellation, and the library. **No test touches a real provider** — that
requires spending money and is the one thing this suite cannot prove.

## First five minutes

This is the walkthrough from `docs/API_CONTRACT.md`, and every step below is a
route that now exists. It is also what `tests/test_studio.py` exercises, so if
it works here it is because the suite says it does. With no real provider key
to spend, steps 2 onward use `adapter_type: "mock"`; everything it produces is
labelled a placeholder at the model, the job and the asset.

| # | what you do | route | what you should see |
|---|---|---|---|
| 1 | **Register.** `POST /api/auth/register` with `{email, password, display_name}`. | `/api/auth/register` | `user`, a personal `workspace`, and a `studio_csrf` cookie whose value every later POST repeats in `X-CSRF-Token`. |
| 2 | **Add a connection.** `POST /api/connections` with `{name, adapter_type, base_url, credential, auth_scheme, models:[]}`. While there is no real provider key to spend, use `adapter_type: "mock"` with `auth_scheme: "none"` — it renders a placeholder file locally and labels it as one. | `/api/connections` | `connection` with `credential.present: true` and only the last four characters of the key (for the mock, `present: false` and no key id, because there is no key). The key itself never comes back. |
| 3 | **Test it.** `POST /api/connections/{id}/test`. | `/api/connections/{id}/test` | `test.outcome` — one of `ok`/`auth_failed`/`endpoint_not_found`/`timeout`/... — plus `http_status`, `latency_ms` and `models_seen`. The outcome is stored with a timestamp, so "last tested" is an observation. |
| 4 | **Register a model.** `POST /api/connections/{id}/models` with a `ModelInput`: `model_key`, `display_name`, `modality`, the `modes` it really supports, and its `parameter_schema`. | `/api/connections/{id}/models` | `model` with `provider.is_mock` telling you whether anything it produces is real. A connection alone cannot generate anything. |
| 5 | **Generate.** `POST /api/generations/images` with `{model_id, mode:"text_to_image", prompt, count:1, client_request_id:<a uuid>}`. | `/api/generations/images` | `job` in state `queued`. No percentage is shown: the job reports progress only if the provider did. Reuse the same `client_request_id` and you get the same job back instead of a second charge. |
| 6 | **Download.** Poll `GET /api/jobs/{id}` until `state` is `completed`, then `GET /api/assets/{outputs[0].asset_id}/content`. | `/api/jobs/{id}`, `/api/assets/{id}/content` | The bytes, with the `Content-Type` taken from the stored signature rather than from whatever the provider claimed. |

If `model.provider.is_mock` was true at step 4, everything from step 5 onward
is a locally rendered placeholder. It is labelled at the model, the job and the
asset. It is never a real generation.

## Troubleshooting

| symptom | cause |
|---|---|
| `ModuleNotFoundError: No module named 'studio'` | you are not in the repository root; `python -c "import studio"` must work from there |
| `configuration_required` (503) on a credential call | `cryptography` is not installed, so `SecretBox` cannot be built |
| `network_restricted` when testing a connection | the base URL resolves to loopback/private space. Add the host to `STUDIO_ALLOW_PRIVATE_HOSTS` or to the connection's `allowed_hosts` |
| `insecure_url` | `http://` outside development. Set `STUDIO_ALLOW_INSECURE_ENDPOINTS=true` for a lab |
| every state-changing request answered `forbidden` | missing `X-CSRF-Token`, or the `Origin` header is not in `STUDIO_PUBLIC_APP_URL` / `STUDIO_API_BASE_URL` |
| credentials stopped working after moving the data directory | the generated key files did not travel with it. This is why you set `STUDIO_ENCRYPTION_KEY` explicitly |
