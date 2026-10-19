"""Database access: one small layer over SQLite *or* Postgres, plus a migration runner.

Why one class and not an abstract base
--------------------------------------
``repository.py`` and its callers hold ``Database`` handles and one place does
``isinstance(db, Database)``. A second class behind a protocol would have meant
touching every caller to accept it, and the seam would sit in a hundred places
instead of one. So the engine is chosen in the constructor from a connection
URL, and the methods branch where the engines genuinely differ. Everything above
this layer -- five repositories, ten service modules, all 619 tests -- is written
against the same method names either way.

Choosing the engine
-------------------
``STUDIO_DATABASE_URL`` set to a ``postgresql://`` URL means Postgres; anything
else means SQLite at the configured path. ``Database.from_config()`` is the
single place that decision is made, so the server, the tests and any script get
the same answer from the same configuration.

What Postgres changes here, and only this
-----------------------------------------
* placeholders ``?`` -> ``%s`` and literal ``%`` -> ``%%`` (see ``sql_dialect``)
* ``INSERT OR IGNORE`` -> ``ON CONFLICT DO NOTHING``
* constraint failures arrive as ``psycopg.errors.*`` rather than
  ``sqlite3.IntegrityError``, and are re-raised as **one** studio type
  (``StudioIntegrityError``) so callers stop caring which engine they are on
* a failed statement aborts the whole transaction, so ``write_tx`` marks the
  transaction dead and rolls back rather than running further statements
* transactions are ``BEGIN`` with ``READ COMMITTED``, and the job claim uses
  ``SELECT ... FOR UPDATE SKIP LOCKED`` (see ``repository.py``)
* schema introspection reads ``information_schema`` rather than ``PRAGMA``

Time and boolean storage are unchanged: both engines store ``TEXT`` ISO-8601
timestamps and ``INTEGER`` 0/1 flags, because the schema says so. That is
deliberate -- it keeps one set of migration files and one set of row shapes.

Concurrency
-----------
The HTTP server is threaded and the job workers run in their own threads, so
connections are thread-local. SQLite uses WAL plus a busy timeout so readers
proceed while a write is in flight, and writes open with ``BEGIN IMMEDIATE`` so
two workers cannot both believe they claimed the same job. Postgres gets the
same guarantee a better way: ``SELECT ... FOR UPDATE SKIP LOCKED``, so a second
worker skips a row another transaction already holds instead of waiting for it.
``docs/SCHEMA.md`` records the decision and the differences to expect.

Transactions nest
-----------------
:class:`Transaction` is re-entrant per thread. A service method can wrap its
work in ``write_tx()`` and call lower-level helpers that also open a
transaction; the inner one joins the outer one instead of raising "cannot start
a transaction within a transaction" or, worse, committing half of it early.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import sql_dialect
from .util import new_id, now_iso

try:  # psycopg is only installed where Postgres is actually used.
    import psycopg
    from psycopg import errors as pg_errors

    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover - the SQLite path never needs it
    psycopg = None  # type: ignore[assignment]
    pg_errors = None  # type: ignore[assignment]
    PSYCOPG_AVAILABLE = False

try:
    from psycopg_pool import ConnectionPool

    PSYCOPG_POOL_AVAILABLE = True
except ImportError:  # pragma: no cover - optional, falls back to direct connects
    ConnectionPool = None  # type: ignore[assignment]
    PSYCOPG_POOL_AVAILABLE = False


class StudioIntegrityError(Exception):
    """A constraint refused the write, whichever engine raised it.

    Callers catch this instead of ``sqlite3.IntegrityError``. The message keeps
    the engine's own text, because ``_conflict_for`` reads it to tell UNIQUE from
    CHECK from FOREIGN KEY -- and because a maintainer debugging a refused insert
    wants to see what the database actually said.
    """


def is_integrity_error(error: BaseException) -> bool:
    """True when *error* is a constraint violation from either engine."""
    if isinstance(error, StudioIntegrityError):
        return True
    if isinstance(error, sqlite3.IntegrityError):
        return True
    if PSYCOPG_AVAILABLE and pg_errors is not None:
        return isinstance(error, pg_errors.IntegrityError)
    return False


def _postgres_error(exc: BaseException) -> Exception:
    """Re-raise an engine error as the studio's own type where that helps.

    Every psycopg error inside a transaction is *also* fatal to that transaction
    (Postgres aborts it), which SQLite does not do. Callers that catch and carry
    on -- "this row already exists, use the existing one" -- therefore have to
    retry on a fresh transaction. Surfacing ``StudioIntegrityError`` lets them
    distinguish that from a connection failure, which is not retryable here.
    """
    if PSYCOPG_AVAILABLE and pg_errors is not None:
        if isinstance(exc, pg_errors.IntegrityError):
            return StudioIntegrityError(str(exc))
    return exc


class Row:
    """A result row that answers both access styles.

    ``sqlite3.Row`` supports ``row["column"]``, ``row[0]``, iteration and
    ``dict(row)``, and the studio's code uses all four. A psycopg dict_row
    supports the first and ``dict()`` but not positional access, and the studio
    does read ``row[0]`` in one place. Rather than remember which is which at
    every call site, Postgres rows come back as this: the same surface SQLite
    offers, built from the cursor description.
    """

    __slots__ = ("_names", "_values", "_index")

    def __init__(self, names: Tuple[str, ...], values: Tuple[Any, ...]) -> None:
        self._names = names
        self._values = values
        self._index = {name: position for position, name in enumerate(names)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> Tuple[str, ...]:
        return self._names

    def values(self) -> Tuple[Any, ...]:
        return self._values

    def items(self) -> Tuple[Tuple[str, Any], ...]:
        return tuple(zip(self._names, self._values))

    def get(self, key: str, default: Any = None) -> Any:
        position = self._index.get(key)
        return default if position is None else self._values[position]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pairs = ", ".join("%s=%r" % item for item in zip(self._names, self._values))
        return "Row(%s)" % pairs

LOGGER = logging.getLogger("studio.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: Columns the insert/update helpers fill in automatically -- *when the table
#: actually has them*. Not every table needs an updated_at (an append-only log
#: row does not), so the helpers check the real column list rather than assuming
#: every table is built the same way.
TIMESTAMP_COLUMNS = ("created_at", "updated_at")


class MigrationError(RuntimeError):
    """A migration file is missing, unreadable, or failed to apply."""


def _connect(path: Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    target = str(path)
    if target != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        target,
        timeout=busy_timeout_ms / 1000.0,
        isolation_level=None,  # transactions are explicit, below
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = %d" % busy_timeout_ms)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:  # in-memory databases cannot use WAL
        pass
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def iter_statements(sql: str) -> Iterator[str]:
    """Split a migration file into executable statements.

    sqlite3's own tokenizer decides where a statement ends, so a semicolon inside
    a string literal or a trigger body does not split anything. We split rather
    than call executescript(), because executescript() commits any open
    transaction before it runs, which would make "apply the schema and record
    that it was applied" two separate commits.
    """
    buffer: List[str] = []
    for line in sql.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                yield statement
            buffer = []
    remainder = "".join(buffer).strip()
    if remainder:
        yield remainder


def discover_migrations(directory: Optional[Path] = None) -> List[Path]:
    """Ordered, deterministic list of migration files. Names must sort correctly."""
    folder = directory or MIGRATIONS_DIR
    if not folder.is_dir():
        raise MigrationError("migrations directory not found: %s" % folder)
    files = sorted(p for p in folder.iterdir() if p.suffix == ".sql")
    if not files:
        raise MigrationError("no migrations found in %s" % folder)
    seen: Dict[str, Path] = {}
    for path in files:
        version = path.stem.split("_", 1)[0]
        if version in seen:
            raise MigrationError(
                "two migrations share version %s: %s and %s"
                % (version, seen[version].name, path.name)
            )
        seen[version] = path
    return files


class Transaction:
    """A unit of work on one connection.

    Only the outermost transaction issues BEGIN/COMMIT; nested uses join it. That
    is what makes it safe for a route handler to open a transaction and then call
    three service functions that each open their own.
    """

    __slots__ = ("_db", "_immediate", "_conn", "_owner", "_closed")

    def __init__(self, db: "Database", immediate: bool) -> None:
        self._db = db
        self._immediate = immediate
        self._conn: Optional[sqlite3.Connection] = None
        self._owner = False
        self._closed = False

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "Transaction":
        conn = self._db.connection()
        self._conn = conn
        state = self._db._tx_state()
        if state["depth"] == 0:
            if self._db.is_postgres:
                # autocommit is on, so BEGIN has to be explicit. A nested
                # savepoint is not needed: depth is tracked here, and the only
                # requirement is that inner blocks join the outer transaction.
                conn.execute("BEGIN")
            else:
                conn.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")
            state["depth"] = 1
            state["aborted"] = False
            self._owner = True
            self._db._observe_transaction(self, conn)
        else:
            state["depth"] += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._closed = True
        state = self._db._tx_state()
        aborted = bool(state.get("aborted"))
        if state["depth"] > 0:
            state["depth"] -= 1
        if not self._owner:
            return False
        assert self._conn is not None
        if exc_type is None and not aborted:
            try:
                self._conn.execute("COMMIT")
            except Exception as error:  # pragma: no cover - defensive
                LOGGER.error("commit failed, rolling back: %s", error)
                with contextlib.suppress(Exception):
                    self._conn.execute("ROLLBACK")
                self._db._forget_transaction(self)
                raise _postgres_error(error) from error
        else:
            with contextlib.suppress(Exception):
                self._conn.execute("ROLLBACK")
        self._db._forget_transaction(self)
        return False

    def mark_aborted(self) -> None:
        """Record that the engine rejected a statement in this transaction.

        Postgres abandons the whole transaction on the first failed statement:
        every later statement raises "current transaction is aborted". A caller
        that catches a constraint error and wants to carry on has to do so in a
        *new* transaction, so this flag makes the enclosing block roll back
        cleanly instead of trying to COMMIT a doomed one.
        """
        self._db._tx_state()["aborted"] = True

    def aborted(self) -> bool:
        return bool(self._db._tx_state().get("aborted"))

    # -- pass-through so a transaction *is* the handle you use ---------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self._db.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Any:
        return self._db.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        return self._db.query(sql, params)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        return self._db.query_one(sql, params)

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        return self._db.scalar(sql, params, default)

    def insert(self, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
        return self._db.insert(table, values)

    def update(
        self,
        table: str,
        record_id: str,
        values: Dict[str, Any],
        *,
        id_column: str = "id",
        touch_updated_at: bool = True,
    ) -> int:
        return self._db.update(
            table,
            record_id,
            values,
            id_column=id_column,
            touch_updated_at=touch_updated_at,
        )


class Database:
    """Owns the schema and the per-thread connections, on SQLite or Postgres."""

    def __init__(
        self,
        path: Path = Path(":memory:"),
        busy_timeout_ms: int = 5000,
        *,
        url: str = "",
    ) -> None:
        """Open a database. A ``postgresql://`` URL wins over *path*.

        Keeping both arguments on one constructor is what makes the switch a
        configuration change rather than a code change: ``Database(path)`` still
        means what it always meant, and ``Database(url=...)`` is the same layer
        on a networked server.
        """
        self.url = (url or "").strip()
        self.is_postgres = self.url.split(":", 1)[0].lower() in (
            "postgresql",
            "postgres",
        )
        if self.is_postgres and not PSYCOPG_AVAILABLE:
            raise MigrationError(
                "STUDIO_DATABASE_URL points at Postgres but psycopg is not "
                "installed; install psycopg[binary] (see requirements.txt)"
            )
        self.path = Path(path) if str(path) != ":memory:" else path
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._all_connections: List[Any] = []
        self._lock = threading.RLock()
        self._applied: List[str] = []
        # Column lists, read once per table. The schema only changes during
        # migrate(), which runs before the server accepts traffic, so caching
        # here removes an introspection query from every single insert.
        self._columns: Dict[str, frozenset] = {}
        self._pool: Any = None
        if self.is_postgres and PSYCOPG_POOL_AVAILABLE:
            # min_size 1 so a cold start does not open ten connections for one
            # health check; max_size bounds the damage a burst can do to the
            # server's max_connections. 12 is comfortably inside the smallest
            # Supabase pooler allowance and far above what one process needs.
            self._pool = ConnectionPool(
                self.url,
                min_size=1,
                max_size=12,
                open=True,
                kwargs={"autocommit": True, "connect_timeout": 15},
            )

    @classmethod
    def from_config(cls, settings: Any) -> "Database":
        """Build from a studio ``Configuration``, honouring the database URL.

        One decision point: everything that needs a database asks here, so the
        server, the runner and the scripts cannot disagree about which engine
        they are on.
        """
        database = settings.database
        return cls(
            settings.database_path,
            database.busy_timeout_ms,
            url=getattr(database, "url", "") or "",
        )

    # -- connections --------------------------------------------------------
    def connection(self) -> Any:
        """This thread's handle.

        SQLite gets a thread-local file connection, which is nearly free. Postgres
        gets a connection checked out of a bounded pool and checked back in when
        the thread releases it -- see :meth:`release`.

        The pool matters more than it looks. The stdlib HTTP server is one thread
        per request, so a thread-local model on Postgres opens a *new* server
        connection per request and never closes it: under load that exhausts
        ``max_connections`` and the whole site starts answering 500 with
        "sorry, too many clients already". That is measured, not theorised -- the
        first Postgres test run died exactly there. The pool bounds it.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self.is_postgres:
                conn = self._checkout()
            else:
                conn = _connect(self.path, self.busy_timeout_ms)
            self._local.conn = conn
            with self._lock:
                self._all_connections.append(conn)
        return conn

    def _checkout(self) -> Any:
        if self._pool is not None:
            return self._pool.getconn()
        return psycopg.connect(self.url, autocommit=True, connect_timeout=15)

    def release(self) -> None:
        """Return this thread's Postgres connection to the pool.

        Called at the end of every HTTP request by the server. Without it a pooled
        connection is only recovered when the pool times it out, so a busy hour
        still leaks. On SQLite this does nothing: the file connection is cheap and
        the thread is about to end anyway.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._lock:
            if conn in self._all_connections:
                self._all_connections.remove(conn)
        if self.is_postgres and self._pool is not None:
            with contextlib.suppress(Exception):
                self._pool.putconn(conn)
        elif self.is_postgres:
            with contextlib.suppress(Exception):
                conn.close()

    def close_all(self) -> None:
        with self._lock:
            connections = list(self._all_connections)
            self._all_connections.clear()
        for conn in connections:
            if self.is_postgres and self._pool is not None:
                with contextlib.suppress(Exception):
                    self._pool.putconn(conn)
            else:
                with contextlib.suppress(Exception):
                    conn.close()
        if self._pool is not None:
            with contextlib.suppress(Exception):
                self._pool.close()
            self._pool = None
        self._local = threading.local()

    def _tx_state(self) -> Dict[str, Any]:
        state = getattr(self._local, "tx", None)
        if state is None:
            state = {"depth": 0, "current": None, "aborted": False}
            self._local.tx = state
        return state

    def _observe_transaction(self, tx: Transaction, conn: Any) -> None:
        # Present so subclasses and instrumentation have a hook that runs exactly
        # once per outermost transaction.
        self._tx_state()["current"] = tx

    def _forget_transaction(self, tx: Transaction) -> None:
        state = self._tx_state()
        state["current"] = None
        state["aborted"] = False

    def in_transaction(self) -> bool:
        return self._tx_state()["depth"] > 0

    @contextlib.contextmanager
    def write_tx(self) -> Iterator[Transaction]:
        """A write transaction.

        SQLite takes its write lock here (``BEGIN IMMEDIATE``); Postgres opens a
        plain ``BEGIN`` and relies on row locks for the same guarantee.
        """
        with Transaction(self, immediate=True) as tx:
            yield tx

    @contextlib.contextmanager
    def read_tx(self) -> Iterator[Transaction]:
        """A read handle. Opens no transaction; reads are consistent enough."""
        with Transaction(self, immediate=False) as tx:
            yield tx

    # -- SQL translation ----------------------------------------------------
    def _sql(self, sql: str, params: Sequence[Any] = ()) -> str:
        """The statement in this engine's spelling."""
        if not self.is_postgres:
            return sql
        return sql_dialect.to_postgres_sql(sql, has_params=bool(params))

    def _params(self, params: Sequence[Any]) -> Any:
        """Parameters in the shape this driver wants."""
        return tuple(params)

    # -- query helpers ------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        """Every row, as something that answers both row["col"] and row[0]."""
        cursor = self.connection().execute(self._sql(sql, params), self._params(params))
        if not self.is_postgres:
            return list(cursor.fetchall())
        columns = tuple(description[0] for description in cursor.description or ())
        return [Row(columns, tuple(values)) for values in cursor.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def query_dicts(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.query(sql, params)]

    def query_dict(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self.query_one(sql, params)
        return dict(row) if row is not None else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0] if not isinstance(row, dict) else next(iter(row.values()))
        return default if value is None else value

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self.connection().execute(self._sql(sql, params), self._params(params))

    def _write(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Execute a statement, recording engine rejection before re-raising.

        The only difference from :meth:`execute` is the ``mark_aborted`` call:
        on Postgres a failed statement poisons the transaction, so the enclosing
        ``write_tx`` must roll back rather than attempt a COMMIT.
        """
        try:
            return self.execute(sql, params)
        except Exception as error:
            if is_integrity_error(error) and self.in_transaction():
                tx = self._tx_state().get("current")
                if tx is not None:
                    tx.mark_aborted()
            raise _postgres_error(error) from error

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Any:
        return self.connection().executemany(
            self._sql(sql, ("x",)), tuple(tuple(r) for r in rows)
        )

    def columns_of(self, table: str) -> frozenset:
        """The actual column set of a table, cached after the first look."""
        cached = self._columns.get(table)
        if cached is None:
            cached = frozenset(self.table_columns(table))
            with self._lock:
                self._columns[table] = cached
        return cached

    def forget_columns(self, table: Optional[str] = None) -> None:
        """Drop cached column lists. Called after a migration changes the schema."""
        with self._lock:
            if table is None:
                self._columns.clear()
            else:
                self._columns.pop(table, None)

    def insert(self, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a row, filling id/created_at/updated_at when the table has them.

        A caller that supplies its own value always wins; the defaults only fill
        gaps. Columns the table does not have are dropped rather than sent, which
        is what lets one helper serve tables with different lifecycles.
        """
        payload = dict(values)
        payload.setdefault("id", new_id())
        payload.setdefault("created_at", now_iso())
        payload.setdefault("updated_at", payload["created_at"])
        known = self.columns_of(table)
        payload = {name: value for name, value in payload.items() if name in known}
        columns = ", ".join('"%s"' % name for name in payload)
        placeholders = ", ".join("?" for _ in payload)
        sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (table, columns, placeholders)
        self._write(sql, list(payload.values()))
        return payload

    def insert_many(self, table: str, rows: Iterable[Dict[str, Any]]) -> int:
        count = 0
        for row in rows:
            self.insert(table, row)
            count += 1
        return count

    def update(
        self,
        table: str,
        record_id: str,
        values: Dict[str, Any],
        *,
        id_column: str = "id",
        touch_updated_at: bool = True,
    ) -> int:
        payload = dict(values)
        if touch_updated_at:
            payload.setdefault("updated_at", now_iso())
        known = self.columns_of(table)
        payload = {name: value for name, value in payload.items() if name in known}
        if not payload:
            return 0
        assignments = ", ".join('"%s" = ?' % name for name in payload)
        sql = 'UPDATE "%s" SET %s WHERE "%s" = ?' % (table, assignments, id_column)
        cursor = self.execute(sql, list(payload.values()) + [record_id])
        return cursor.rowcount

    def update_where(
        self,
        table: str,
        values: Dict[str, Any],
        where: str,
        params: Sequence[Any] = (),
        *,
        touch_updated_at: bool = True,
    ) -> int:
        """Update every row matching the given predicate. Callers pass their own WHERE."""
        payload = dict(values)
        if touch_updated_at:
            payload.setdefault("updated_at", now_iso())
        known = self.columns_of(table)
        payload = {name: value for name, value in payload.items() if name in known}
        if not payload:
            return 0
        assignments = ", ".join('"%s" = ?' % name for name in payload)
        sql = 'UPDATE "%s" SET %s WHERE %s' % (table, assignments, where)
        return self.execute(sql, list(payload.values()) + list(params)).rowcount

    def delete(self, table: str, record_id: str, *, id_column: str = "id") -> int:
        return self.execute(
            'DELETE FROM "%s" WHERE "%s" = ?' % (table, id_column), [record_id]
        ).rowcount

    def count(self, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
        sql = 'SELECT COUNT(*) FROM "%s"' % table
        if where:
            sql += " WHERE " + where
        return int(self.scalar(sql, params, default=0))

    def exists(self, table: str, where: str, params: Sequence[Any] = ()) -> bool:
        sql = 'SELECT 1 FROM "%s" WHERE %s LIMIT 1' % (table, where)
        return self.query_one(sql, params) is not None

    def table_columns(self, table: str) -> List[str]:
        if self.is_postgres:
            rows = self.query(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = ?"
                " ORDER BY ordinal_position",
                (table,),
            )
            return [row[0] for row in rows]
        return [row["name"] for row in self.query('PRAGMA table_info("%s")' % table)]

    # -- migrations ---------------------------------------------------------
    @property
    def applied_versions(self) -> List[str]:
        return list(self._applied)

    def migrate(
        self,
        directory: Optional[Path] = None,
        *,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> List[str]:
        """Apply every pending migration, once, in order.

        Each file runs inside one transaction together with the bookkeeping row
        that records it, so an interrupted run cannot leave a half-applied
        version marked done. Statements are split and executed one at a time
        rather than through executescript(), which would commit first.

        The same seven files serve both engines. They were written with ``TEXT``
        ids, ``TEXT`` ISO-8601 timestamps and ``INTEGER`` 0/1 flags precisely so
        that they would, which is why there is no second migration set to keep
        in step with this one.
        """
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                checksum    TEXT NOT NULL,
                applied_at  TEXT NOT NULL
            )
            """
        )
        already = {
            row["version"] for row in self.query("SELECT version FROM schema_migrations")
        }
        applied_now: List[str] = []
        for path in discover_migrations(directory):
            version = path.stem.split("_", 1)[0]
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            statements = (
                sql_dialect.split_statements(sql)
                if self.is_postgres
                else iter_statements(sql)
            )
            with self.write_tx() as tx:
                try:
                    for statement in statements:
                        tx.execute(statement)
                    tx.execute(
                        "INSERT INTO schema_migrations"
                        " (version, filename, checksum, applied_at) VALUES (?, ?, ?, ?)",
                        (version, path.name, checksum, now_iso()),
                    )
                except Exception as exc:
                    raise MigrationError("migration %s failed: %s" % (path.name, exc)) from exc
            applied_now.append(version)
            LOGGER.info("applied migration %s", path.name)
            if on_progress:
                on_progress(version)
        self._applied = sorted(already | set(applied_now))
        # Migrations may have added tables or columns; any cached column list is
        # now stale and must be re-read.
        self.forget_columns()
        # A migration edited after it ran is a real hazard: the running schema no
        # longer matches the file everyone is reading.
        self._verify_checksums(directory)
        return applied_now

    def _verify_checksums(self, directory: Optional[Path]) -> None:
        folder = directory or MIGRATIONS_DIR
        recorded = {
            row["version"]: (row["filename"], row["checksum"])
            for row in self.query("SELECT version, filename, checksum FROM schema_migrations")
        }
        for path in discover_migrations(folder):
            version = path.stem.split("_", 1)[0]
            entry = recorded.get(version)
            if not entry:
                continue
            checksum = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if entry[1] != checksum:
                raise MigrationError(
                    "migration %s has changed since it was applied; add a new migration "
                    "instead of editing an applied one" % path.name
                )

    # -- housekeeping -------------------------------------------------------
    def ping(self) -> bool:
        try:
            return self.scalar("SELECT 1", default=0) == 1
        except Exception:
            return False

    def table_names(self) -> List[str]:
        if self.is_postgres:
            rows = self.query(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
            )
            return sorted(row[0] for row in rows)
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return sorted(row["name"] for row in rows)

    def vacuum(self) -> None:
        """Compact the store. On Postgres this is VACUUM, which cannot run inside
        a transaction, so it is executed on the autocommit connection."""
        self.execute("VACUUM")

    def checkpoint(self) -> None:
        """Flush the SQLite WAL. A no-op on Postgres, which has no WAL file here."""
        if self.is_postgres:
            return
        with contextlib.suppress(sqlite3.Error):
            self.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def rows_to_dicts(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]
