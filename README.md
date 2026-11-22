# NQL — ask your database in plain English

NQL connects to your PostgreSQL or MySQL/MariaDB database, reads its schema, and turns
questions like *"which 5 customers spent the most last year?"* into SQL. It can also run the
query and show the results. It runs on your machine, so it can reach local and private
databases, and your connection details never leave it.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\nql
```

A browser tab opens at http://localhost:8765. You'll need two things:

1. **An LLM key.** The free Gemini key from https://aistudio.google.com/apikey works. Paste it
   into **Settings**, or put `GEMINI_API_KEY=...` in a `.env` file (see `.env.example`).
2. **A database URL**, e.g. `postgresql://user:pass@localhost:5432/mydb` or
   `mysql://user:pass@localhost:3306/shop`.

No database to try it on? `docker compose up -d` starts seeded demo databases:
`postgresql://demo:demo@localhost:55432/shop` and `mysql://demo:demo@localhost:53306/shop`.

### Docker

```powershell
docker build -t nql .
docker run --rm -p 127.0.0.1:8765:8765 -e GEMINI_API_KEY=your-key nql
```

From inside the container, a database on your own machine is `host.docker.internal`, not `localhost`.

## How it works

1. **Introspect.** Reads tables, columns, types, primary and foreign keys, comments, and the
   distinct values of low-cardinality text columns (so the model writes `status = 'delivered'`,
   not `'Delivered'`).
2. **Pick tables.** Schemas with more than 25 tables get a cheap first LLM call that picks the
   relevant tables. Their foreign-key neighbours are added.
3. **Generate.** The compact schema, the dialect, today's date, recent questions (for follow-ups)
   and the question go to the LLM.
4. **Check.** `sqlglot` parses the SQL and rejects anything that isn't a single SELECT,
   including DML hidden in CTEs, `SELECT INTO`, `FOR UPDATE`, and functions like `pg_sleep`
   or `LOAD_FILE`.
5. **Run or EXPLAIN.** The query runs in a **read-only transaction** with a statement timeout
   and row limit, and is always rolled back. If the database reports an error, the error goes
   back to the model to fix the query (up to 2 retries).

**Safety layers:** the SQL checker, the read-only transaction, and (recommended) a database
user that only has `SELECT` privileges. The server only listens on `127.0.0.1` unless you pass
`--host`.

## Other LLM providers

Any OpenAI-compatible API works. Set the base URL, key and model in Settings or `.env`:

| Provider | Base URL | Notes |
|---|---|---|
| Gemini (default) | `https://generativelanguage.googleapis.com/v1beta/openai/` | free tier; default model `gemini-flash-latest` |
| Groq | `https://api.groq.com/openai/v1` | free tier, very fast |
| OpenRouter | `https://openrouter.ai/api/v1` | `:free` models |
| Ollama | `http://localhost:11434/v1` | fully local, any key text, e.g. `qwen2.5-coder` |

Settings saved from the UI are stored in `~/.nql/settings.json` and override `.env`.

## Development

```powershell
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

Tests use SQLite and a scripted fake LLM. To also run them on real servers, start
`docker compose up -d` and set:

```powershell
$env:NQL_TEST_POSTGRES_URL="postgresql://demo:demo@127.0.0.1:55432/shop"; $env:NQL_TEST_MYSQL_URL="mysql://demo:demo@127.0.0.1:53306/shop"
```

| File | Role |
|---|---|
| `nql/schema.py` | introspection and the schema text given to the LLM |
| `nql/guard.py` | read-only SQL checks |
| `nql/executor.py` | read-only, time-limited execution |
| `nql/pipeline.py` | prompts, table selection, generate→check→fix loop |
| `nql/llm.py` | OpenAI-compatible client |
| `nql/server.py` | FastAPI app and `nql` command |
| `nql/static/index.html` | the UI |
