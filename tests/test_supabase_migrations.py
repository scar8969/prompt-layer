"""Phase 1: the two engines must agree about the schema, not just the queries.

A migration suite can pass on Postgres and still be wrong: ``CREATE TABLE IF NOT
EXISTS`` swallows a difference, a column type can land differently, and an index
can be missing without any statement failing. These tests compare the *result* of
the seven migrations on both engines rather than trusting that both runs exited
zero.

They are skipped when ``STUDIO_TEST_POSTGRES`` is unset, because comparing two
schemas needs two schemas.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from studio.db import Database  # noqa: E402


def _sqlite_schema(tmp_path):
    db = Database(tmp_path / "parity.db")
    db.migrate()
    return db


def _columns(db):
    """{table: [column, ...]} for every user table, sorted."""
    tables = [name for name in db.table_names() if name != "schema_migrations"]
    return {name: db.table_columns(name) for name in tables}


@pytest.fixture()
def both(tmp_path, studio_database):
    if not studio_database:
        pytest.skip("set STUDIO_TEST_POSTGRES to compare the two schemas")
    sqlite_db = _sqlite_schema(tmp_path)
    pg_db = Database(url=studio_database)
    pg_db.migrate()
    try:
        yield sqlite_db, pg_db
    finally:
        sqlite_db.close_all()
        pg_db.close_all()


class TestSchemasMatch:
    def test_both_engines_have_the_same_tables(self, both):
        sqlite_db, pg_db = both
        sqlite_tables = set(sqlite_db.table_names()) - {"schema_migrations"}
        pg_tables = set(pg_db.table_names()) - {"schema_migrations"}
        assert sqlite_tables == pg_tables, (
            "only in sqlite: %s | only in postgres: %s"
            % (sqlite_tables - pg_tables, pg_tables - sqlite_tables)
        )

    def test_every_table_has_the_same_columns_in_the_same_order(self, both):
        sqlite_db, pg_db = both
        sqlite_columns = _columns(sqlite_db)
        pg_columns = _columns(pg_db)
        assert set(sqlite_columns) == set(pg_columns)
        mismatched = {
            table: (sqlite_columns[table], pg_columns[table])
            for table in sqlite_columns
            if sqlite_columns[table] != pg_columns[table]
        }
        assert not mismatched, "column lists differ: %s" % mismatched

    def test_the_same_number_of_tables_exist(self, both):
        sqlite_db, pg_db = both
        assert len(_columns(sqlite_db)) == len(_columns(pg_db))

    def test_no_migration_is_recorded_as_unapplied_on_either_engine(self, both):
        sqlite_db, pg_db = both
        assert sqlite_db.applied_versions == pg_db.applied_versions
        assert len(pg_db.applied_versions) == 7
