# Changelog

All notable changes to this repository, with the date and the scope of
the change. The repo's two products are siblings: **Prompt Layer**
(`/api/` + `index.html`, the Vercel-shaped preflight tool) and
**Studio** (`studio/` + `web/`, the BYOK creative platform).

## Unreleased

### Phase 1 of the Vercel migration: Studio runs on Postgres

`studio/db.py` now speaks Postgres as well as SQLite, behind the same interface.
Choosing the engine is one value -- `STUDIO_DATABASE_URL` -- and
`Database.from_config()` is the only place that decides. The same seven
migrations apply to both; there is no second schema.

**Verified, not assumed:** the full suite passes on SQLite (639 passed, 14
skipped) *and* against a real PostgreSQL 17 server (653 passed, 0 skipped, 0
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
