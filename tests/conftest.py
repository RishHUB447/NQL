"""Shared fixtures.

Tests run against SQLite by default. Set NQL_TEST_POSTGRES_URL and/or
NQL_TEST_MYSQL_URL (e.g. the docker-compose demo DBs) to run them on real servers too.
"""

import os
from pathlib import Path

import pytest
import sqlalchemy as sa

from nql.executor import Executor
from nql.server import normalize_url

SEED = (Path(__file__).parent.parent / "examples" / "seed.sql").read_text()


def _sqlite_engine(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'shop.db'}")
    with engine.begin() as conn:
        for stmt in SEED.split(";"):
            if stmt.strip():
                conn.exec_driver_sql(stmt)
    return engine


_BACKENDS = ["sqlite"] + [
    name for name, var in (("postgres", "NQL_TEST_POSTGRES_URL"), ("mysql", "NQL_TEST_MYSQL_URL"))
    if os.getenv(var)
]


@pytest.fixture(params=_BACKENDS)
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = _sqlite_engine(tmp_path)
    else:
        var = "NQL_TEST_POSTGRES_URL" if request.param == "postgres" else "NQL_TEST_MYSQL_URL"
        eng = sa.create_engine(normalize_url(os.environ[var]))  # pre-seeded by docker-compose
    yield eng
    eng.dispose()


@pytest.fixture
def executor(engine):
    return Executor(engine, row_limit=5, timeout_ms=5000)


class FakeLLM:
    """Returns scripted replies in order and records what it was sent."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[tuple[str, list[dict]]] = []

    def chat(self, system, messages):
        self.calls.append((system, list(messages)))
        return self.replies.pop(0)
