"""Local web app: FastAPI backend + single-page UI. Start with `nql`."""

import argparse
import json
import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path

import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings, load_settings
from .executor import Executor, QueryError
from .guard import UnsafeSQL
from .llm import LLMError, OpenAICompatibleLLM
from .pipeline import NQL

STATIC = Path(__file__).parent / "static"
USER_SETTINGS = Path.home() / ".nql" / "settings.json"
LLM_FIELDS = ("llm_base_url", "llm_api_key", "llm_model")

DRIVERS = {
    "postgres": "postgresql+psycopg",
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mysql+pymysql",
    "sqlite": "sqlite",  # handy for demos and tests
}


def normalize_url(url: str) -> sa.URL:
    """Accept plain `postgres://` / `mysql://` URLs and pick our bundled drivers."""
    try:
        parsed = sa.make_url(url.strip())
    except sa.exc.ArgumentError as e:
        raise ValueError(f"Not a valid database URL: {e}") from e
    backend = parsed.drivername.split("+")[0]
    if backend not in DRIVERS:
        raise ValueError("Use a postgresql://, mysql:// or mariadb:// URL.")
    return parsed.set(drivername=DRIVERS[backend])


class SettingsLLM:
    """Builds the LLM client from the current settings, so changes apply immediately."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: OpenAICompatibleLLM | None = None
        self._key: tuple | None = None

    def chat(self, system: str, messages: list[dict]) -> str:
        s = self.settings
        # Gemini fallback models only make sense when talking to Gemini.
        fallbacks = s.llm_fallback_models if "googleapis.com" in s.llm_base_url else []
        key = (s.llm_base_url, s.llm_api_key, s.llm_model, tuple(fallbacks))
        if self._client is None or key != self._key:
            self._client = OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model, fallbacks)
            self._key = key
        return self._client.chat(system, messages)


class State:
    def __init__(self) -> None:
        self.settings = load_settings()
        self._load_user_settings()
        self.llm = SettingsLLM(self.settings)
        self.nql: NQL | None = None
        self.lock = threading.Lock()

    def _load_user_settings(self) -> None:
        try:
            saved = json.loads(USER_SETTINGS.read_text())
        except (OSError, ValueError):
            return
        for f in LLM_FIELDS:
            if saved.get(f):
                setattr(self.settings, f, saved[f])

    def save_user_settings(self) -> None:
        USER_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        USER_SETTINGS.write_text(json.dumps({f: getattr(self.settings, f) for f in LLM_FIELDS}))

    def connect(self, url: str) -> None:
        engine = sa.create_engine(
            normalize_url(url), pool_pre_ping=True, pool_size=2, max_overflow=2,
            connect_args=_connect_args(url),
        )
        executor = Executor(engine, self.settings.row_limit, self.settings.statement_timeout_ms)
        nql = NQL(executor, self.llm, self.settings.max_fix_attempts,
                  self.settings.max_tables_in_prompt)  # introspects; raises if unreachable
        with self.lock:
            old, self.nql = self.nql, nql
        if old:
            old.executor.engine.dispose()

    def require_nql(self) -> NQL:
        if self.nql is None:
            raise HTTPException(400, "Connect to a database first.")
        return self.nql


def _connect_args(url: str) -> dict:
    return {} if url.strip().startswith("sqlite") else {"connect_timeout": 10}


state = State()
app = FastAPI(title="NQL", version=__version__)


class ConnectBody(BaseModel):
    url: str


class AskBody(BaseModel):
    question: str
    execute: bool = True
    history: list[dict] = []


class RunBody(BaseModel):
    sql: str


class SettingsBody(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


def _status() -> dict:
    s, nql = state.settings, state.nql
    return {
        "connected": nql is not None,
        "database": nql.executor.engine.url.render_as_string(hide_password=True) if nql else None,
        "dialect": nql.dialect_label if nql else None,
        "table_count": len(nql.tables) if nql else 0,
        "llm_model": s.llm_model,
        "llm_base_url": s.llm_base_url,
        "has_api_key": bool(s.llm_api_key),
        "row_limit": s.row_limit,
    }


def _result_json(result) -> dict | None:
    return asdict(result) if result else None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return _status()


@app.post("/api/connect")
def connect(body: ConnectBody):
    try:
        state.connect(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except sa.exc.SQLAlchemyError as e:
        raise HTTPException(400, f"Could not connect: {getattr(e, 'orig', None) or e}")
    return _status()


@app.post("/api/disconnect")
def disconnect():
    with state.lock:
        old, state.nql = state.nql, None
    if old:
        old.executor.engine.dispose()
    return _status()


@app.post("/api/schema/refresh")
def refresh_schema():
    state.require_nql().refresh_schema()
    return _status()


@app.get("/api/schema")
def get_schema():
    nql = state.require_nql()
    return [
        {
            "name": t.qualified_name,
            "columns": [
                {"name": c.name, "type": c.type, "pk": c.primary_key, "values": c.values}
                for c in t.columns
            ],
            "foreign_keys": [asdict(fk) for fk in t.foreign_keys],
        }
        for t in nql.tables
    ]


@app.post("/api/ask")
def ask(body: AskBody):
    nql = state.require_nql()
    if not body.question.strip():
        raise HTTPException(400, "Type a question first.")
    try:
        a = nql.ask(body.question.strip(), execute=body.execute, history=body.history)
    except LLMError as e:
        raise HTTPException(502, str(e))
    return {
        "question": a.question,
        "sql": a.sql,
        "explanation": a.explanation,
        "error": a.error,
        "result": _result_json(a.result),
        "attempts": [asdict(x) for x in a.attempts],
        "tables_used": a.tables_used,
    }


@app.post("/api/run")
def run(body: RunBody):
    """Run SQL the user edited by hand (same read-only checks)."""
    nql = state.require_nql()
    try:
        return {"result": _result_json(nql.executor.run(body.sql)), "error": None}
    except (UnsafeSQL, QueryError) as e:
        return {"result": None, "error": str(e)}


@app.post("/api/settings")
def update_settings(body: SettingsBody):
    for f in LLM_FIELDS:
        value = getattr(body, f)
        if value is not None and value.strip():
            setattr(state.settings, f, value.strip())
    state.save_user_settings()
    return _status()


def main() -> None:
    parser = argparse.ArgumentParser(description="NQL: ask your database in plain English.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind (default 127.0.0.1 = this machine only).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab.")
    args = parser.parse_args()

    if state.settings.database_url:
        try:
            state.connect(state.settings.database_url)
            print(f"Connected to {_status()['database']}")
        except (ValueError, sa.exc.SQLAlchemyError) as e:
            print(f"Could not connect to NQL_DATABASE_URL: {e}")

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: NQL is reachable from other machines and runs SQL with your "
              "database credentials. Only do this on a trusted network.")
    url = f"http://localhost:{args.port}"
    print(f"NQL running at {url}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
