"""Shared fixtures: an optional Postgres backend for the whole suite.

Why this exists
---------------
The studio's tests build their own ``Configuration`` from an explicit mapping --
deliberately, so one test's settings cannot leak into another's. That design
means ``STUDIO_DATABASE_URL`` in the process environment is *not* seen by them,
which is correct for isolation and inconvenient for "run everything on Postgres".

So the switch is explicit too, and it is one environment variable:

    STUDIO_TEST_POSTGRES=postgresql://postgres@127.0.0.1:5432/postgres

When it is set, :func:`studio_database` hands each test module its own freshly
created database on that server and drops it afterwards. When it is not set the
fixture yields ``""`` and every test keeps using the SQLite file it always used.

The databases are created by connecting to the admin database named in the URL,
so nothing needs to exist in advance beyond a role that may CREATE DATABASE.
Names carry the module and a random suffix: two pytest runs cannot collide, and
a crashed run leaves nothing that a later run will trip over.
"""

from __future__ import annotations

import os
import uuid

import pytest

POSTGRES_ENV = "STUDIO_TEST_POSTGRES"


def _admin_connect(url: str):
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(url, autocommit=True, connect_timeout=15)


def _server_url(url: str, database: str) -> str:
    """The same server, a different database.

    A connection URL is ``scheme://user:pass@host:port/dbname?params``, so the
    database name is replaced by splitting on the last ``/`` before any query
    string. Doing it this way keeps the credentials, host, port and options
    exactly as the caller wrote them.
    """
    head, separator, tail = url.partition("?")
    base = head.rsplit("/", 1)[0]
    query = separator + tail if separator else ""
    return "%s/%s%s" % (base, database, query)


@pytest.fixture(scope="session")
def postgres_admin_url() -> str:
    return os.environ.get(POSTGRES_ENV, "").strip()


@pytest.fixture(scope="module")
def studio_database(postgres_admin_url, request) -> str:
    """A database URL for this test module, or "" to mean SQLite.

    Yields a ``postgresql://`` URL when ``STUDIO_TEST_POSTGRES`` is set, otherwise
    the empty string. Tests pass the value straight into their configuration
    mapping as ``STUDIO_DATABASE_URL``; an empty value is what ``load_config``
    already treats as "use the file on disk".
    """
    if not postgres_admin_url:
        yield ""
        return

    module = request.module.__name__.replace("test_", "").replace("_", "-")
    name = "studio_test_%s_%s" % (module, uuid.uuid4().hex[:8])
    with _admin_connect(postgres_admin_url) as admin:
        admin.execute('CREATE DATABASE "%s"' % name)
    try:
        yield _server_url(postgres_admin_url, name)
    finally:
        with _admin_connect(postgres_admin_url) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute('DROP DATABASE IF EXISTS "%s"' % name)
