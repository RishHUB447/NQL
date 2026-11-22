"""Runs checked queries inside a read-only, time-limited transaction."""

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .guard import check_read_only, with_row_limit


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


class QueryError(Exception):
    """The database rejected the query. The message is fed back to the LLM."""


def _json_value(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (decimal.Decimal, uuid.UUID, dt.timedelta)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    return str(v)


def _db_error_message(e: sa.exc.SQLAlchemyError) -> str:
    msg = str(getattr(e, "orig", None) or e).strip()
    return msg[:800]


class Executor:
    def __init__(self, engine: Engine, row_limit: int, timeout_ms: int):
        self.engine = engine
        self.row_limit = row_limit
        self.timeout_ms = timeout_ms

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def _begin_read_only(self, conn: sa.Connection) -> None:
        d = self.dialect
        if d == "postgresql":
            conn.exec_driver_sql("BEGIN READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
        elif d == "mysql":
            try:  # MySQL
                conn.exec_driver_sql(f"SET SESSION max_execution_time = {int(self.timeout_ms)}")
            except sa.exc.DBAPIError:  # MariaDB uses seconds and a different name
                conn.exec_driver_sql(
                    f"SET SESSION max_statement_time = {self.timeout_ms / 1000:.3f}"
                )
            conn.exec_driver_sql("START TRANSACTION READ ONLY")
        elif d == "sqlite":
            conn.exec_driver_sql("PRAGMA query_only = ON")
            conn.exec_driver_sql("BEGIN")
        else:
            raise QueryError(f"Unsupported database: {d}")

    def run(self, sql: str) -> QueryResult:
        """Check, limit and execute ``sql``. Raises UnsafeSQL or QueryError."""
        stmt = check_read_only(sql, self.dialect)
        limited = with_row_limit(sql, stmt, self.row_limit + 1)
        return self._execute(limited, fetch=True)

    def explain(self, sql: str) -> None:
        """Validate ``sql`` against the real schema without running it."""
        check_read_only(sql, self.dialect)
        self._execute("EXPLAIN " + sql.strip().rstrip(";"), fetch=False)

    def _execute(self, sql: str, fetch: bool) -> QueryResult:
        # AUTOCOMMIT lets us issue BEGIN READ ONLY ourselves; we always roll back.
        # no_parameters stops drivers treating '%' in LIKE patterns as placeholders.
        with self.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT", no_parameters=True
        ) as conn:
            try:
                self._begin_read_only(conn)
                result = conn.exec_driver_sql(sql)
                if not fetch or not result.returns_rows:
                    return QueryResult([], [], False)
                columns = list(result.keys())
                raw = result.fetchmany(self.row_limit + 1)
                rows = [[_json_value(v) for v in r] for r in raw[: self.row_limit]]
                return QueryResult(columns, rows, truncated=len(raw) > self.row_limit)
            except sa.exc.SQLAlchemyError as e:
                raise QueryError(_db_error_message(e)) from e
            finally:
                try:
                    conn.exec_driver_sql("ROLLBACK")
                    if self.dialect == "sqlite":
                        conn.exec_driver_sql("PRAGMA query_only = OFF")
                except sa.exc.SQLAlchemyError:
                    conn.invalidate()  # never return a connection in an unknown state
