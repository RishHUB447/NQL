"""Static checks that a generated statement is a single read-only query.

This is one of two layers. The other is that queries always run inside a
read-only transaction (see executor.py). A read-only database user is the
recommended third layer.
"""

import sqlglot
from sqlglot import exp

SQLGLOT_DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}

_WRITE_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
        "TruncateTable", "Command", "Into", "Lock", "Grant", "Copy", "Set", "Use",
    )
    if hasattr(exp, name)
)
_QUERY_ROOTS = tuple(
    getattr(exp, name) for name in ("Select", "SetOperation", "Union") if hasattr(exp, name)
)
# Functions that read files, sleep, or reach other servers.
_BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "lo_import", "lo_export", "dblink", "dblink_exec", "pg_terminate_backend",
    "pg_cancel_backend", "sleep", "benchmark", "load_file", "sys_exec", "sys_eval",
}


class UnsafeSQL(ValueError):
    pass


def sqlglot_dialect(dialect_name: str) -> str:
    return SQLGLOT_DIALECTS.get(dialect_name, dialect_name)


def check_read_only(sql: str, dialect_name: str) -> exp.Expression:
    """Parse ``sql`` and return its tree, or raise UnsafeSQL."""
    dialect = sqlglot_dialect(dialect_name)
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"Could not parse SQL: {e}") from e
    if len(statements) != 1:
        raise UnsafeSQL(f"Expected exactly one statement, got {len(statements)}.")

    stmt = statements[0]
    if not isinstance(stmt, _QUERY_ROOTS):
        raise UnsafeSQL(f"Only SELECT queries are allowed, got {stmt.key.upper()}.")
    for node in stmt.walk():
        if isinstance(node, _WRITE_NODES):
            raise UnsafeSQL(f"Statement contains a disallowed {node.key.upper()} clause.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _BLOCKED_FUNCTIONS:
                raise UnsafeSQL(f"Function {name}() is not allowed.")
    return stmt


def with_row_limit(sql: str, stmt: exp.Expression, limit: int) -> str:
    """``sql`` with a LIMIT appended if the top-level query has none.

    Appending text (rather than regenerating SQL from the tree) keeps the executed
    query identical to what the user sees. The newline guards against a trailing
    ``-- comment`` swallowing the LIMIT.
    """
    sql = sql.strip().rstrip(";").rstrip()
    if stmt.args.get("limit") is not None or stmt.args.get("fetch") is not None:
        return sql
    return f"{sql}\nLIMIT {limit}"
