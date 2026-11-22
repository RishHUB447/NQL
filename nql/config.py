"""Settings read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    # Tried in order when the main model is overloaded, rate-limited or not found.
    llm_fallback_models: list[str]
    database_url: str
    row_limit: int
    statement_timeout_ms: int
    max_fix_attempts: int
    # Schemas larger than this get a table-selection step before SQL generation.
    max_tables_in_prompt: int


def load_settings() -> Settings:
    load_dotenv()
    env = os.getenv
    return Settings(
        llm_base_url=env("NQL_LLM_BASE_URL", GEMINI_OPENAI_URL),
        llm_api_key=env("NQL_LLM_API_KEY") or env("GEMINI_API_KEY", ""),
        llm_model=env("NQL_LLM_MODEL", "gemini-flash-latest"),
        llm_fallback_models=[
            m.strip()
            for m in env("NQL_LLM_FALLBACK_MODELS", "gemini-flash-lite-latest").split(",")
            if m.strip()
        ],
        database_url=env("NQL_DATABASE_URL", ""),
        row_limit=int(env("NQL_ROW_LIMIT", "200")),
        statement_timeout_ms=int(env("NQL_STATEMENT_TIMEOUT_MS", "10000")),
        max_fix_attempts=int(env("NQL_MAX_FIX_ATTEMPTS", "2")),
        max_tables_in_prompt=int(env("NQL_MAX_TABLES_IN_PROMPT", "25")),
    )
