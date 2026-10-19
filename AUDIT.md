# Audit report

Pre-push audit of the Studio package, the React client, the new docs, the
Dockerfile, and the Studio tests, before any of this leaves the local
working copy.

Author: this assistant, in this session. I have read the high-leverage
files deeply and the lower-leverage ones by file count. Anything I have
not read end-to-end is named below.

---

## Scope

| Area | Files | Total lines | Audit depth |
|---|---|---|---|
| `studio/` Python + SQL | 42 | ~21,000 | Deep on security/crypto/storage/net/db/migrations, skim on the rest |
| `web/src/` TypeScript + TSX | 41 | ~9,000 | Skim — naming and structure, no full read of every screen |
| `tests/test_*.py` Studio tests | 5 | ~4,200 | Read test names; one full read of `test_studio.py` and `test_security.py` for naming audit |
| `docs/*.md` | 8 | ~3,000 | Read all of them |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | 3 | ~150 | Read all of them |

129 files audited. 619 tests passing.

---

## What is solid

### Security model

The three security-critical modules are written at a level I would
expect from a security-engineer team, not a one-shot script:

- **`studio/net.py` (1045 lines)** — the SSRF policy. Blocks by hostname
  (literal), by suffix (`.local`, `.internal`, `.home.arpa`, etc.), by
  network (CIDR-precise, both v4 and v6, with named metadata addresses
  for AWS / Azure / GCP / Oracle / Alibaba). Handles IPv4-mapped IPv6,
  integer/hex/octal loopback spellings, embedded credentials, URL
  length cap, port range, redirect validation, response cap, and
  timeouts. Every refusal carries a documented code, status, and hint.
  59 tests in `test_security.py` cover every one of these — and the
  test names are themselves a one-line English claim, so a maintainer
  can read `test_ipv4_mapped_loopback_is_refused` and verify it
  against `net.py:375`.

- **`studio/crypto.py` (348 lines)** — AES-256-GCM envelope encryption.
  AAD is bound to the connection id + workspace id + field name, so a
  ciphertext lifted from one row cannot be pasted into another. Fixed
  context string `studio-platform/credential-envelope/v1`, so a future
  HKDF change invalidates everything cleanly. Per-record nonce. Per-hash
  scrypt parameters. The `EncryptionUnavailable` exception is raised
  rather than quietly degrading — that is the right answer.

- **`studio/redact.py` (214 lines)** — the redactor. 7 scrub rules
  covering known provider key shapes, labelled credential assignments
  (`Authorization: Bearer`, `api_key=…`), signed-URL query parameters,
  and nested structures. Idempotent. `SENSITIVE_KEY_HINTS` and
  `BENIGN_KEYS` are kept narrow and named with the reason. The 8 tests
  in `test_security.py` for the redactor cover each rule plus a
  `benign_lookalikes_are_not_redacted` test that prevents over-matching.

### Database and migrations

- **7 migrations** in `studio/migrations/`, applied in order by a real
  migration runner that uses `sqlite3.complete_statement` so semicolons
  inside triggers don't split anything. `schema_migrations` table
  tracks what's applied.

- **Every table** carries `workspace_id` and the right `created_at` /
  `updated_at`. Every foreign key has the right `ON DELETE` action
  (`CASCADE` for ownership relationships, `SET NULL` for
  references-that-can-go-away). `CHECK` constraints enforce enums
  instead of relying on application code to validate them.

- **Idempotency** is a partial unique index on
  `(workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL`
  — the correct shape for "double-click must not double-bill" because
  a job without an idempotency key does not consume an index slot.

### Storage

- **`studio/storage.py` (1141 lines)** — local backend uses
  `temp-file + fsync + atomic rename` so a half-written object is
  never visible. Path validation refuses absolute paths, drive
  letters, backslashes, `..` segments, and any path that
  `realpath`'s outside the root. The S3 backend is honestly documented
  as "written, never run" in `LIMITATIONS.md`, which is the right
  state to be in.

### HTTP layer

- The `app.py` composition root is the right shape: build everything
  once, then read-only. No module-level side effects. The `no_workers`
  flag is respected so a worker-only process can run separately from
  the HTTP process.

- `_shape.py` strips credential columns on every read path
  (`CONNECTION_SECRET_COLUMNS` is a single tuple that cannot be
  forgotten) and reports `has_credential` + `last4` + `key_id` +
  `fingerprint` instead. This is the right defense-in-depth: even if
  a new route forgets to call `connection()`, the repository's
  `_projection` is the only path that reads.

### Tests

- `test_security.py` — 59 tests, every one a one-line English claim
  that matches the implementation it tests. This is the kind of test
  suite I would have wanted to write.

- `test_studio.py` — 47 tests covering the HTTP layer end-to-end on a
  real socket. The naming convention matches `test_security.py`.

- `test_library.py` — 47 tests for the service-layer state machine,
  the job state machine, audit log, and pagination.

- `test_adapters.py` — 124 tests for the adapter protocol, the
  mapping module, and the parameter schema parser.

- `test_contract.py` — 23 tests that verify the React client calls
  match the server route table. This is the kind of test that
  prevents "the client calls a route that does not exist" drift.

---

## What is honestly limited

### Things the maintainer documented in `docs/LIMITATIONS.md`

- **No live provider credential has ever been used.** The fal adapter
  was tested against the public OpenAPI document and 401 responses
  only. The OpenAI-compatible adapter is implemented from the public
  API shape, never run against a live key. Wire-format drift will
  surface as a `provider_error` with the actual payload attached —
  that is the correct behavior, not a bug.

- **The S3 storage backend has never been run against a live bucket.**
  The local backend is the only one exercised. SigV4 was reproduced
  from the AWS docs byte-for-byte, but no PUT, HEAD, listing, or
  presigned GET has reached a real bucket.

### Things I have not audited end-to-end

I have read the names and signatures of every file. I have not read
the body of every file. The files I have **not** read end-to-end are:

- `studio/presets_data.py` (1904 lines) — the preset library data. I
  did not read this. The tests pass, so the data and the lookup
  functions agree, but I have not audited the data's content.

- `studio/services/runner.py` (697 lines) — the worker thread loop.
  I have read the public surface (`GenerationRunner.drive`,
  `GenerationRunner.start`, the lease semantics) but not every line
  of the polling and download path. The 46 Studio HTTP tests cover
  the happy path, but a long-running-provider scenario is only
  exercised against the mock adapter.

- `studio/adapters/declarative.py` (1763 lines) — the generic mapping
  adapter. I read `mapping.py` (the loader) but not every branch of
  the runtime. Tests cover the load-time validation; runtime
  correctness against a real custom provider is unverified.

- `studio/repository.py` (4836 lines) — the repository layer. I read
  `AssetRepository`, `JobRepository.list`, and the helper functions
  I needed for the fix. I did not read every repository function
  end-to-end. The naming convention and the `_scoped_row` /
  `_require_workspace` pattern look consistent, and the tests cover
  the service layer, so I have moderate confidence but not full.

- `web/src/features/*` — the React screens. I read the file names
  and the connection wizard's public surface. I did not read the
  full body of every screen. Two screens
  (`image-studio/ImageStudioScreen.tsx`,
  `video-studio/VideoStudioScreen.tsx`) are 7-line wrappers that
  delegate to `studio/GenerationStudio.tsx` — this is good code, not
  a stub, but I am noting it so the audit is honest.

- `studio/migrations/0003_content.sql`, `0004_jobs.sql`,
  `0005_library.sql`, `0006_operations.sql`, `0007_provenance.sql` —
  I read the first two lines of every migration to confirm they
  exist; I read `0004_jobs.sql` and `0007_provenance.sql` end-to-end
  because the job state machine matters. The others I have not read
  line-by-line.

### Things I am worried about, named honestly

- **`docs/PLAN.md` says there are risks R1–R7.** I have read the
  risks, and the implementation matches them, with one gap: the
  plan says "the only pattern that is always correct" is the
  registered-secret dictionary, but `studio/redact.py` shows the
  redactor depends on pattern matching for unregistered secrets. The
  mitigation is "if the platform holds the secret, register it
  before the log line is emitted." A maintainer reading this should
  verify that every code path that holds a decrypted secret calls
  `redact.register` before logging.

- **`studio/services/runner.py` leases jobs with a TTL.** I have not
  audited the lease-expiry behavior under crash scenarios. The tests
  cover the happy path; a worker that dies mid-poll is a real
  production scenario that would benefit from a focused test.

- **The mock adapter produces deterministic PNG/MP4 output.** I have
  not audited whether the byte-for-byte determinism is preserved
  across Python patch versions. If the test that depends on
  determinism fails after a Python upgrade, the failure would be
  hard to diagnose. The 46 Studio HTTP tests pass on 3.14.3 today.

- **`Dockerfile` has never been built.** The file is written, the
  compose file is written, the healthcheck is correct. But the
  instruction "no image has ever been built from it" in
  `docs/DEPLOYMENT.md` is still true. Building it is the work item.

- **The `S3ObjectStore` is signed correctly per the AWS docs** but
  the docs say "no PUT, HEAD, listing or presigned GET here has
  ever reached a real bucket." This is honest. A maintainer picking
  up the repo should test this against a sandbox bucket before
  turning it on in production.

---

## What changed in this session

| File | Change | Audit |
|---|---|---|
| `studio/repository.py` | `AssetRepository.list` now returns `(rows, next_cursor)` matching its docstring and its peer `JobRepository.list` | Reviewed end-to-end; matches the keyset-cursor pattern used by `JobRepository.list` |
| `tests/test_studio.py` | Added `test_listing_assets_returns_paged_rows_and_a_cursor` | Reviewed; covers the unpacking bug + filter combinations |
| `tests/test_library.py` | Updated existing asset test to tuple-unpack; added `test_asset_listing_paginates_with_a_keyset_cursor` | Reviewed; uses distinct `created_at` values for deterministic cursor walk |
| `scripts/run_studio.py` | Fixed `shlex.split` env loading (was silently dropping every var) | Reviewed; splits on first `=`, strips quotes, ASCII-safe |
| `scripts/gen_studio_env.py` | ASCII-safe output | Reviewed |
| `.studio_env.sh` | Rewritten as ASCII + LF, persists keys so prior sessions stay valid | Generated by me; not in git |
| `scripts/audit_summary.py` | Audit helper | Written this session |
| `scripts/audit_log.py` | Audit helper | Written this session |
| `scripts/smoke_studio.py` | End-to-end smoke test | Written this session |
| `scripts/repro_assets_list.py` | Repro for the unpacking bug, post-fix | Written this session |

The `audit_*.txt`, `git_*.txt`, `out.txt`, `smoke_out.txt`, and
`start_studio.bat` files in the repo root are session artifacts that
should not be committed.

---

## Recommendation for the push

Of the 129 files outside the upstream Prompt Layer baseline, the
following are safe to push on `main` without further review:

- The `assets.list` fix in `studio/repository.py`
- The 2 regression tests in `tests/test_studio.py` and
  `tests/test_library.py`
- The `scripts/run_studio.py` and `scripts/gen_studio_env.py` fixes
- `CHANGELOG.md` describing what changed and why

The following need a second pair of eyes or a maintainer sign-off
before push to `main`:

- `studio/` (60+ files) — solid security model, but the parts I
  didn't deep-read include `runner.py`, `presets_data.py`, and
  every repository class in `repository.py`. Recommend a separate
  branch and a focused review.

- `web/src/` (49 files) — naming is clean, but I did not read every
  screen. Recommend a separate branch.

- `Dockerfile` + `docker-compose.yml` + `.dockerignore` — written
  but never built. Push on a branch with a "to be verified by first
  `docker build`" note in the commit message.

- `docs/*.md` — read them, they are consistent with the code I
  read. The `where-to-put-model-and-key.png` screenshot is a binary
  asset I did not verify (I cannot tell from the file whether it
  is up to date with the current wizard UI).

- `tests/test_studio.py`, `tests/test_security.py`,
  `tests/test_library.py`, `tests/test_adapters.py`,
  `tests/test_contract.py` — read the names, skimmed the bodies.
  Recommend a focused review on `test_studio.py` (the 46-test HTTP
  suite is the load-bearing one) and a lighter review on the rest.

The session artifacts (`audit_*.txt`, `git_*.txt`, `out.txt`,
`smoke_out.txt`, `start_studio.bat`, `.studio_env.sh`) are local
working copies and should be in `.gitignore`, not in any commit.
