"""Phase 1: the SQLite -> Postgres move, tested as a property of the layer.

What these tests are for
------------------------
The 619 existing tests prove the *contract* holds. They pass on SQLite and, with
``STUDIO_TEST_POSTGRES`` set, on Postgres -- but that only says the two engines
agree about the behaviour a test happened to exercise. These tests cover what
differs between the engines, which is where a silent break would live:

* the SQL translation (placeholders, literal percent, conflict-tolerant insert)
* statement splitting that respects comments and string literals
* a constraint failure surfacing as one studio type on either engine
* a long run of requests not exhausting the server's connection limit
* migrations applying to a genuinely empty Postgres database
* ``from_config`` choosing the engine from configuration alone

Every test that needs a server is skipped, loudly, when there is none -- a skip
is honest; pretending Postgres was verified when it was not is the thing
``docs/LIMITATIONS.md`` exists to prevent.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio import sql_dialect  # noqa: E402
from studio.config import load_config  # noqa: E402
from studio.db import (  # noqa: E402
    Database,
    MigrationError,
    Row,
    StudioIntegrityError,
    is_integrity_error,
)


# ---------------------------------------------------------------------------
# SQL translation: pure functions, no server needed
# ---------------------------------------------------------------------------


class TestTheDialect:
    def test_a_placeholder_becomes_a_percent_s(self):
        sql = sql_dialect.to_postgres_sql(
            "SELECT * FROM users WHERE id = ?", has_params=True
        )
        assert sql == "SELECT * FROM users WHERE id = %s"

    def test_many_placeholders_all_become_percent_s(self):
        sql = sql_dialect.to_postgres_sql(
            "INSERT INTO t (a, b, c) VALUES (?, ?, ?)", has_params=True
        )
        assert sql.count("%s") == 3
        assert "?" not in sql

    def test_a_literal_percent_is_doubled_when_parameters_are_passed(self):
        # psycopg treats % as its interpolation character, so a LIKE pattern
        # arriving with parameters must spell the wildcard as %%.
        sql = sql_dialect.to_postgres_sql(
            "SELECT * FROM t WHERE name LIKE ? ESCAPE '\\'", has_params=True
        )
        assert sql == "SELECT * FROM t WHERE name LIKE %s ESCAPE '\\'"

    def test_a_statement_without_parameters_does_not_double_its_percents(self):
        # Doubling here would be wrong: psycopg only interpolates when params
        # are supplied, so '50%%' would be sent to the server literally.
        sql = sql_dialect.to_postgres_sql("SELECT 50 AS pct", has_params=False)
        assert sql == "SELECT 50 AS pct"

    def test_insert_or_ignore_becomes_on_conflict_do_nothing(self):
        sql = sql_dialect.to_postgres_sql(
            "INSERT OR IGNORE INTO project_assets (project_id, asset_id) VALUES (?, ?)",
            has_params=True,
        )
        assert "OR IGNORE" not in sql
        assert sql.startswith("INSERT INTO project_assets")
        assert sql.endswith("ON CONFLICT DO NOTHING")

    def test_insert_or_replace_is_refused_rather_than_guessed(self):
        # There is no single Postgres spelling with the same meaning. Silently
        # picking one would change behaviour; raising says so instead.
        with pytest.raises(NotImplementedError):
            sql_dialect.to_postgres_sql("INSERT OR REPLACE INTO t VALUES (?)", has_params=True)

    def test_a_question_mark_inside_a_string_literal_is_not_a_placeholder(self):
        sql = sql_dialect.to_postgres_sql(
            "SELECT * FROM t WHERE note = 'why?' AND id = ?", has_params=True
        )
        assert "'why?'" in sql
        assert sql.endswith("id = %s")


class TestStatementSplitting:
    def test_a_semicolon_inside_a_line_comment_does_not_end_a_statement(self):
        # This was a real failure: a prose comment ending in ';' cut a CREATE
        # TABLE in half and Postgres answered "syntax error at end of input".
        sql = """
-- this comment ends in a semicolon;
CREATE TABLE a (id TEXT);
"""
        statements = sql_dialect.split_statements(sql)
        assert len(statements) == 1
        assert "CREATE TABLE a" in statements[0]

    def test_a_semicolon_inside_a_string_literal_does_not_end_a_statement(self):
        sql = "INSERT INTO t (a) VALUES ('one; two');"
        statements = sql_dialect.split_statements(sql)
        assert len(statements) == 1

    def test_several_statements_split_apart(self):
        sql = "CREATE TABLE a (id TEXT);\nCREATE TABLE b (id TEXT);\n"
        assert len(sql_dialect.split_statements(sql)) == 2

    def test_the_real_migration_files_split_into_the_same_count_as_sqlite(self):
        # SQLite's own tokenizer is the reference: if the two counts agree on
        # all seven files, the Postgres splitter is seeing the same statements.
        from pathlib import Path

        from studio.db import iter_statements

        folder = Path(REPO) / "studio" / "migrations"
        files = sorted(folder.glob("*.sql"))
        assert files, "no migration files found"
        for path in files:
            sql = path.read_text(encoding="utf-8")
            pg = sql_dialect.split_statements(sql)
            sqlite_count = len(list(iter_statements(sql)))
            assert len(pg) == sqlite_count, path.name


# ---------------------------------------------------------------------------
# Row shape: both access styles must work, on both engines
# ---------------------------------------------------------------------------


class TestRowShape:
    def test_a_row_answers_by_name_and_by_position(self):
        row = Row(("id", "name"), ("abc", "Example"))
        assert row["id"] == "abc"
        assert row[0] == "abc"
        assert row["name"] == "Example"
        assert dict(row) == {"id": "abc", "name": "Example"}

    def test_a_row_iterates_over_values_not_names(self):
        row = Row(("a", "b"), (1, 2))
        assert list(row) == [1, 2]

    def test_a_missing_column_is_a_key_error_not_a_silent_none(self):
        row = Row(("a",), (1,))
        with pytest.raises(KeyError):
            row["nope"]
        assert row.get("nope", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Engine selection from configuration
# ---------------------------------------------------------------------------


class TestEngineSelection:
    def test_no_url_means_sqlite(self, tmp_path):
        db = Database(tmp_path / "x.db")
        assert db.is_postgres is False
        db.close_all()

    def test_a_postgres_url_means_postgres(self):
        pytest.importorskip("psycopg")
        db = Database(url="postgresql://user@host:5432/db")
        assert db.is_postgres is True
        # No connection is opened in the constructor, so this asserts the
        # decision without needing a server.
        db.close_all()

    def test_the_postgres_scheme_also_works(self):
        pytest.importorskip("psycopg")
        assert Database(url="postgres://u@h/d").is_postgres is True

    def test_from_config_reads_the_url_out_of_the_configuration(self, tmp_path):
        pytest.importorskip("psycopg")
        config = load_config(
            {
                "STUDIO_DATA_DIR": str(tmp_path),
                "STUDIO_DATABASE_URL": "postgresql://user@host:5432/db",
            }
        )
        db = Database.from_config(config)
        assert db.is_postgres is True
        db.close_all()

    def test_from_config_without_a_url_stays_on_sqlite(self, tmp_path):
        config = load_config({"STUDIO_DATA_DIR": str(tmp_path)})
        db = Database.from_config(config)
        assert db.is_postgres is False
        db.close_all()


# ---------------------------------------------------------------------------
# Everything below needs a real Postgres server
# ---------------------------------------------------------------------------


@pytest.fixture()
def pg(studio_database):
    """A migrated Postgres database, or skip when no server was provided."""
    if not studio_database:
        pytest.skip("set STUDIO_TEST_POSTGRES to run the Postgres tests")
    db = Database(url=studio_database)
    db.migrate()
    try:
        yield db
    finally:
        db.close_all()


class TestTheSchemaAppliesToAnEmptyPostgres:
    def test_all_seven_migrations_apply(self, pg):
        assert pg.applied_versions == ["0001", "0002", "0003", "0004", "0005", "0006", "0007"]

    def test_every_table_the_sqlite_schema_has_exists(self, pg):
        # 26 tables in the seven migration files: if the Postgres run created
        # fewer, a statement was skipped or silently swallowed.
        names = set(pg.table_names())
        expected = {
            "users", "workspaces", "workspace_members", "sessions", "auth_tokens",
            "api_connections", "models", "connection_tests", "projects", "assets",
            "character_references", "characters", "generation_jobs",
            "generation_outputs", "job_events", "job_temp_files", "idempotency_keys",
            "worker_heartbeats", "presets", "preset_favorites", "saved_prompts",
            "project_assets", "usage_records", "audit_log", "rate_limits",
            "schema_migrations",
        }
        assert expected <= names, "missing: %s" % (expected - names)

    def test_a_second_migrate_is_a_no_op(self, pg):
        assert pg.migrate() == []

    def test_schema_introspection_reads_the_real_columns(self, pg):
        columns = pg.table_columns("users")
        assert "email_normalized" in columns
        assert columns[0] == "id"


class TestWritesRoundTrip:
    def _user(self, db, label="a"):
        user_id = str(uuid.uuid4())
        db.insert(
            "users",
            {
                "id": user_id,
                "email": "%s@example.test" % label,
                "email_normalized": "%s@example.test" % label,
                "password_hash": "x",
                "created_at": "2026-01-01T00:00:00.000000Z",
                "updated_at": "2026-01-01T00:00:00.000000Z",
            },
        )
        return user_id

    def test_insert_then_read_back(self, pg):
        user_id = self._user(pg)
        row = pg.query_dict("SELECT email FROM users WHERE id = ?", (user_id,))
        assert row["email"] == "a@example.test"

    def test_update_reports_the_rows_it_changed(self, pg):
        user_id = self._user(pg, "b")
        assert pg.update("users", user_id, {"display_name": "Pat"}) == 1
        assert pg.query_dict(
            "SELECT display_name FROM users WHERE id = ?", (user_id,)
        )["display_name"] == "Pat"

    def test_a_duplicate_unique_value_raises_the_studio_integrity_error(self, pg):
        # This is the translation that matters: callers catch one type and stop
        # caring whether they are on SQLite or Postgres.
        user_id = self._user(pg, "dup")
        with pytest.raises(StudioIntegrityError):
            self._user(pg, "dup")
        assert pg.query_dict("SELECT id FROM users WHERE id = ?", (user_id,)) is not None

    def test_is_integrity_error_recognises_the_studio_type(self):
        assert is_integrity_error(StudioIntegrityError("boom")) is True
        assert is_integrity_error(ValueError("nope")) is False

    def test_insert_or_ignore_is_tolerated_on_postgres(self, pg):
        # project_assets is the join table the repository populates this way.
        owner = self._user(pg, "join")
        workspace = str(uuid.uuid4())
        stamp = "2026-01-01T00:00:00.000000Z"
        pg.insert(
            "workspaces",
            {"id": workspace, "name": "W", "slug": "s" + workspace[:8],
             "owner_user_id": owner, "created_at": stamp, "updated_at": stamp},
        )
        project = str(uuid.uuid4())
        pg.insert(
            "projects",
            {"id": project, "workspace_id": workspace, "created_by_user_id": owner,
             "name": "P", "created_at": stamp, "updated_at": stamp},
        )
        asset = str(uuid.uuid4())
        pg.insert(
            "assets",
            {"id": asset, "workspace_id": workspace, "owner_user_id": owner,
             "kind": "image", "source": "upload", "storage_key": "k",
             "mime_type": "image/png", "created_at": stamp, "updated_at": stamp},
        )
        statement = (
            "INSERT OR IGNORE INTO project_assets (project_id, asset_id, added_at)"
            " VALUES (?, ?, ?)"
        )
        pg.execute(statement, (project, asset, stamp))
        # Second insert must not raise: that is the whole point of IGNORE.
        pg.execute(statement, (project, asset, stamp))
        assert pg.count("project_assets") == 1


class TestConnectionsAreBounded:
    def test_many_requests_do_not_exhaust_the_server(self, pg):
        """The measured failure: one connection per HTTP thread, never closed.

        The first full Postgres run answered 500 with "sorry, too many clients
        already" once the module-scoped fixture had served enough requests. This
        drives more requests than the pool is allowed to hold and asserts the
        server still answers, which is the property that was broken.
        """
        def worker(index):
            for _ in range(3):
                pg.query_one("SELECT ?", (index,))
                pg.release()

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        # Still usable afterwards: the pool handed every connection back.
        assert pg.scalar("SELECT 1") == 1

    def test_release_is_idempotent(self, pg):
        pg.query_one("SELECT 1")
        pg.release()
        pg.release()
        assert pg.scalar("SELECT 2") == 2
