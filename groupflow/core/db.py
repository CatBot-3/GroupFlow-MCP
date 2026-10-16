"""SQLite access shared by every module.

One connection, guarded by a re-entrant lock, so the MCP server's event loop,
the worker and test code can share it safely. Every engine operation runs in
`tx()`, which opens `BEGIN IMMEDIATE`: the write lock is taken up front, so two
writers can never interleave half-applied changes. Nested `tx()` calls join the
outer transaction.

Modules register their schema with `register_schema`; `Db` applies all of them.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_SCHEMAS: list[tuple[str, str]] = []

# Schemas added after the first release, in the order they appeared. Each only adds tables, so a file
# recorded without some of them is upgraded in place by the same CREATE TABLE IF NOT EXISTS script.
ADDITIVE_SCHEMAS = ("collaboration_v1", "accounts_v1")


def register_schema(name: str, sql: str) -> None:
    """Add a module's CREATE TABLE statements (idempotent: use IF NOT EXISTS)."""
    if all(n != name for n, _ in _SCHEMAS):
        _SCHEMAS.append((name, sql))


class SchemaMismatch(RuntimeError):
    """The database file was made by a different version of GroupFlow."""


def _row_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {d[0]: v for d, v in zip(cursor.description, row)}


class Db:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.raw.row_factory = _row_factory
        self.raw.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.raw.execute("PRAGMA journal_mode = WAL")
            self.raw.execute("PRAGMA busy_timeout = 3000")
        self._lock = threading.RLock()
        self._depth = 0
        self.ensure_schema()

    def ensure_schema(self) -> None:
        # Import side effects register every module's schema before it is applied.
        from groupflow import schemas  # noqa: F401

        want = hashlib.sha256("\n".join(sql for _, sql in _SCHEMAS).encode()).hexdigest()[:16]
        with self._lock:
            tables = {r["name"] for r in self.raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            # CREATE TABLE IF NOT EXISTS never changes a table that is already there, so a file from another version would keep
            # its old columns and checks and fail later in confusing ways. Refuse it up front instead.
            found = None
            if "gf_meta" in tables:
                row = self.raw.execute("SELECT value FROM gf_meta WHERE key = 'schema'").fetchone()
                found = row["value"] if row else None
            if tables and found not in _known_versions():
                self.raw.close()
                raise SchemaMismatch(f"{self.path} has an unsupported schema. Preserve the file and migrate a backup; "
                                     "GroupFlow will not reset or guess its format.")
            # One atomic, additive migration. executescript normally commits first;
            # include BEGIN/COMMIT in the script itself so DDL and marker agree.
            script = "BEGIN IMMEDIATE;\n" + "\n".join(sql for _, sql in _SCHEMAS)
            script += "\nCREATE TABLE IF NOT EXISTS gf_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            script += f"\nINSERT OR REPLACE INTO gf_meta VALUES ('schema', '{want}');\nCOMMIT;"
            try:
                self.raw.executescript(script)
            except BaseException:
                if self.raw.in_transaction:
                    self.raw.execute("ROLLBACK")
                self.raw.close()
                raise

    # ------------------------------------------------------------ queries

    def get(self, sql: str, *params: Any) -> dict[str, Any] | None:
        with self._lock:
            return self.raw.execute(sql, _bind(params)).fetchone()

    def all(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        with self._lock:
            return self.raw.execute(sql, _bind(params)).fetchall()

    def run(self, sql: str, *params: Any) -> int:
        with self._lock:
            return self.raw.execute(sql, _bind(params)).rowcount

    def insert(self, sql: str, *params: Any) -> int:
        with self._lock:
            return int(self.raw.execute(sql, _bind(params)).lastrowid or 0)

    def scalar(self, sql: str, *params: Any) -> Any:
        row = self.get(sql, *params)
        return None if row is None else next(iter(row.values()))

    # ------------------------------------------------------------ transactions

    def tx(self, fn: Callable[[], T]) -> T:
        """Run fn atomically. Nested calls join the outer transaction."""
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    return fn()
                finally:
                    self._depth -= 1
            self.raw.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                result = fn()
                self.raw.execute("COMMIT")
                return result
            except BaseException:
                self.raw.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.raw.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
                self.raw.execute("COMMIT")
            except BaseException:
                self.raw.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    def wipe(self) -> None:
        """Delete every row in every table (demo reset and tests)."""
        with self._lock:
            tables = [r["name"] for r in self.raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            self.raw.execute("PRAGMA foreign_keys = OFF")
            try:
                self.raw.execute("BEGIN IMMEDIATE")
                for t in tables:
                    self.raw.execute(f'DELETE FROM "{t}"')
                if self.raw.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                    self.raw.execute("DELETE FROM sqlite_sequence")
                self.raw.execute("COMMIT")
            except BaseException:
                self.raw.execute("ROLLBACK")
                raise
            finally:
                self.raw.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        with self._lock:
            self.raw.close()


def _schema_hash(skip: set[str]) -> str:
    return hashlib.sha256("\n".join(sql for name, sql in _SCHEMAS if name not in skip).encode()).hexdigest()[:16]


def _known_versions() -> set[str]:
    """The current schema, and every earlier one that lacks only some additive schemas."""
    from itertools import combinations

    present = [n for n in ADDITIVE_SCHEMAS if any(name == n for name, _ in _SCHEMAS)]
    return {_schema_hash(set(skip)) for k in range(len(present) + 1) for skip in combinations(present, k)}


def _bind(params: tuple[Any, ...]) -> tuple[Any, ...]:
    out = []
    for p in params:
        if isinstance(p, bool):
            out.append(1 if p else 0)
        else:
            out.append(p)
    return tuple(out)
