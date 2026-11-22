"""Question -> SQL -> checked -> (executed) answer, with automatic error repair."""

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from . import schema as sch
from .executor import Executor, QueryError, QueryResult
from .guard import UnsafeSQL
from .llm import LLM

DIALECT_NAMES = {"postgresql": "PostgreSQL", "mysql": "MySQL/MariaDB", "sqlite": "SQLite"}

SYSTEM_PROMPT = """You are an expert {dialect} analyst. Write ONE read-only SELECT query \
(CTEs allowed) that answers the user's question using the database schema below.

Rules:
- Use only tables and columns that exist in the schema.
- Use {dialect} syntax and functions.
- When a column lists its `values:`, use those literals exactly (same case and spelling).
- Join on the foreign keys shown (`->`). Give computed columns readable aliases.
- Do not add a LIMIT unless the question asks for a specific number of rows (e.g. "top 5").
- Today's date is {today}; use it for relative dates like "last month".
- If the schema cannot answer the question, reply with exactly: CANNOT_ANSWER: <short reason>

Reply format, nothing else:
```sql
<the query>
```
<one sentence describing what the query returns>

Schema:
{schema}"""

TABLE_PICK_PROMPT = """Below is a list of database tables with their columns. \
Return a JSON array of the table names needed to answer the question, e.g. ["a.orders", "a.users"]. \
Include tables needed for joins. Reply with the JSON array only.

Tables:
{index}"""

FIX_PROMPT = """That query failed:
{error}

Fix it. Reply in the same format (```sql block, then one sentence)."""


@dataclass
class Attempt:
    sql: str
    error: str | None


@dataclass
class Answer:
    question: str
    sql: str | None = None
    explanation: str = ""
    result: QueryResult | None = None
    error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)


def parse_reply(text: str) -> tuple[str | None, str]:
    """Split an LLM reply into (sql, explanation). sql is None for CANNOT_ANSWER."""
    if text.startswith("CANNOT_ANSWER"):
        return None, text.split(":", 1)[-1].strip() or "The schema cannot answer this."
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip(), text[m.end():].strip()
    if re.match(r"\s*(select|with)\b", text, re.I):  # model ignored the fence
        return text.strip(), ""
    return None, text.strip() or "The model returned an empty reply."


class NQL:
    def __init__(self, executor: Executor, llm: LLM, max_fix_attempts: int = 2,
                 max_tables_in_prompt: int = 25):
        self.executor = executor
        self.llm = llm
        self.max_fix_attempts = max_fix_attempts
        self.max_tables_in_prompt = max_tables_in_prompt
        self.tables: list[sch.Table] = []
        self.refresh_schema()

    def refresh_schema(self) -> None:
        self.tables = sch.introspect(self.executor.engine)

    @property
    def dialect_label(self) -> str:
        return DIALECT_NAMES.get(self.executor.dialect, self.executor.dialect)

    def _relevant_tables(self, question: str) -> list[sch.Table]:
        """All tables for small schemas; an LLM-picked subset (plus FK neighbours) for big ones."""
        if len(self.tables) <= self.max_tables_in_prompt:
            return self.tables
        reply = self.llm.chat(
            TABLE_PICK_PROMPT.format(index=sch.render_index(self.tables)),
            [{"role": "user", "content": question}],
        )
        m = re.search(r"\[.*\]", reply, re.S)
        try:
            names = {str(n) for n in json.loads(m.group(0))} if m else set()
        except json.JSONDecodeError:
            names = set()
        known = {t.qualified_name for t in self.tables}
        # Accept bare names too ("orders" for "public.orders").
        by_bare = {t.name: t.qualified_name for t in self.tables}
        chosen = {n if n in known else by_bare.get(n.split(".")[-1], "") for n in names} - {""}
        return sch.with_neighbours(self.tables, chosen) if chosen else self.tables

    def ask(self, question: str, execute: bool = True,
            history: list[dict] | None = None) -> Answer:
        answer = Answer(question=question)
        tables = self._relevant_tables(question)
        answer.tables_used = [t.qualified_name for t in tables]
        system = SYSTEM_PROMPT.format(
            dialect=self.dialect_label,
            today=dt.date.today().isoformat(),
            schema=sch.render(tables),
        )

        messages: list[dict] = []
        for turn in (history or [])[-5:]:  # follow-ups like "now only for 2024"
            if turn.get("question") and turn.get("sql"):
                messages.append({"role": "user", "content": turn["question"]})
                messages.append({"role": "assistant", "content": f"```sql\n{turn['sql']}\n```"})
        messages.append({"role": "user", "content": question})

        for _ in range(1 + self.max_fix_attempts):
            reply = self.llm.chat(system, messages)
            sql, explanation = parse_reply(reply)
            if sql is None:
                answer.error = explanation
                return answer
            answer.sql, answer.explanation = sql, explanation
            try:
                if execute:
                    answer.result = self.executor.run(sql)
                else:
                    self.executor.explain(sql)
                answer.attempts.append(Attempt(sql, None))
                answer.error = None
                return answer
            except (UnsafeSQL, QueryError) as e:
                answer.attempts.append(Attempt(sql, str(e)))
                answer.error = str(e)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": FIX_PROMPT.format(error=e)})
        return answer
