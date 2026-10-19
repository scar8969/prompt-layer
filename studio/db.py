"""Database access: one small layer over SQLite, plus a real migration runner.

Why SQLite
----------
The platform needs durable relational storage, transactions, and a schema under
migration control. SQLite provides all three with no service to install, so a
fresh clone runs with zero infrastructure and the deployment story is "one file
on a volume". The schema is written in ordinary SQL (see studio/migrations/)
and the queries avoid SQLite-only syntax wherever a portable form was just as
clear, so moving to a networked database later is a change of connection layer
rather than a rewrite. `docs/SCHEMA.md` records that decision and the exact
differences to expect.

Concurrency
-----------
The HTTP server is threaded and the job workers run in their own threads, so
connections are thread-local. WAL mode plus a busy timeout lets readers proceed
while a write is in flight, and every write path goes through
:meth:`Database.write_tx`, which opens with BEGIN IMMEDIATE so two workers
cannot both believe they claimed the same job.

Transactions nest
-----------------
:class:`Transaction` is re-entrant per thread. A service method can wrap its
work in `write_tx()` and call lower-level helpers that also open a transaction;
the inner one joins the outer one instead of raising "cannot start a transaction
within a transaction" or, worse, committing half of it early.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

from .util import new_id, now_iso

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
            conn.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")
            state["depth"] = 1
            self._owner = True
            self._db._observe_transaction(self, conn)
        else:
            state["depth"] += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._closed = True
        state = self._db._tx_state()
        if state["depth"] > 0:
            state["depth"] -= 1
        if not self._owner:
            return False
        assert self._conn is not None
        if exc_type is None:
            try:
                self._conn.execute("COMMIT")
            except sqlite3.Error as error:  # pragma: no cover - defensive
                LOGGER.error("commit failed, rolling back: %s", error)
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("ROLLBACK")
                raise
        else:
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("ROLLBACK")
        self._db._forget_transaction(self)
        return False

    # -- pass-through so a transaction *is* the handle you use ---------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self._db.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        return self._db.query(sql, params)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
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
    """Owns the file, the schema, and the per-thread connections."""

    def __init__(self, path: Path, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._all_connections: List[sqlite3.Connection] = []
        self._lock = threading.RLock()
        self._applied: List[str] = []
        # Column lists, read once per table. The schema only changes during
        # migrate(), which runs before the server accepts traffic, so caching
        # here removes a PRAGMA from every single insert.
        self._columns: Dict[str, frozenset] = {}

    # -- connections --------------------------------------------------------
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _connect(self.path, self.busy_timeout_ms)
            self._local.conn = conn
            with self._lock:
                self._all_connections.append(conn)
        return conn

    def close_all(self) -> None:
        with self._lock:
            connections = list(self._all_connections)
            self._all_connections.clear()
        for conn in connections:
            with contextlib.suppress(Exception):
                conn.close()
        self._local = threading.local()

    def _tx_state(self) -> Dict[str, Any]:
        state = getattr(self._local, "tx", None)
        if state is None:
            state = {"depth": 0, "current": None}
            self._local.tx = state
        return state

    def _observe_transaction(self, tx: Transaction, conn: sqlite3.Connection) -> None:
        # Present so subclasses and instrumentation have a hook that runs exactly
        # once per outermost transaction.
        self._tx_state()["current"] = tx

    def _forget_transaction(self, tx: Transaction) -> None:
        self._tx_state()["current"] = None

    def in_transaction(self) -> bool:
        return self._tx_state()["depth"] > 0

    @contextlib.contextmanager
    def write_tx(self) -> Iterator[Transaction]:
        """A write transaction that takes the write lock immediately."""
        with Transaction(self, immediate=True) as tx:
            yield tx

    @contextlib.contextmanager
    def read_tx(self) -> Iterator[Transaction]:
        """A read handle. Opens no transaction; reads are consistent enough on WAL."""
        with Transaction(self, immediate=False) as tx:
            yield tx

    # -- query helpers ------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        return list(self.connection().execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return self.connection().execute(sql, tuple(params)).fetchone()

    def query_dicts(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.query(sql, params)]

    def query_dict(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self.query_one(sql, params)
        return dict(row) if row is not None else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.connection().execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self.connection().executemany(sql, tuple(tuple(r) for r in rows))

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
        self.execute(sql, list(payload.values()))
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
        """
        conn = self.connection()
        conn.execute(
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
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        applied_now: List[str] = []
        for path in discover_migrations(directory):
            version = path.stem.split("_", 1)[0]
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            with self.write_tx() as tx:
                try:
                    for statement in iter_statements(sql):
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
        except sqlite3.Error:
            return False

    def table_names(self) -> List[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return sorted(row["name"] for row in rows)

    def vacuum(self) -> None:
        self.execute("VACUUM")

    def checkpoint(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]
