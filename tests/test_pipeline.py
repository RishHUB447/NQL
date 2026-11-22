import pytest

from nql import schema as sch
from nql.executor import QueryError
from nql.guard import UnsafeSQL
from nql.pipeline import NQL, parse_reply
from tests.conftest import FakeLLM

GOOD = "```sql\nSELECT c.name, COUNT(*) AS orders FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY orders DESC\n```\nOrders per customer."


def t(name, engine):
    """Table name as NQL renders it for this backend."""
    return f"public.{name}" if engine.dialect.name == "postgresql" else name


def test_introspection_finds_keys_and_categorical_values(engine):
    tables = {x.name: x for x in sch.introspect(engine)}
    assert set(tables) >= {"customers", "orders", "order_items", "products"}
    orders = tables["orders"]
    assert [c.name for c in orders.columns if c.primary_key] == ["id"]
    assert orders.foreign_keys[0].ref_table == t("customers", engine)
    status = next(c for c in orders.columns if c.name == "status")
    assert status.values == ["cancelled", "delivered", "pending", "refunded", "shipped"]

    text = sch.render(list(tables.values()))
    assert f"customer_id integer NOT NULL -> {t('customers', engine)}.id" in text
    assert "'refunded'" in text


def test_ask_runs_query_and_returns_rows(executor):
    llm = FakeLLM(GOOD)
    a = NQL(executor, llm).ask("orders per customer")
    assert a.error is None
    assert a.result.columns == ["name", "orders"]
    assert a.result.rows[0] == ["Asha Rao", 3]
    assert a.result.truncated  # 8 customers, row_limit=5
    assert len(a.result.rows) == 5
    assert "TABLE" in llm.calls[0][0]  # schema went into the system prompt


def test_db_error_is_fed_back_and_fixed(executor):
    bad = "```sql\nSELECT nme FROM customers\n```\nNames."
    llm = FakeLLM(bad, GOOD)
    a = NQL(executor, llm).ask("orders per customer")
    assert a.error is None and a.result is not None
    assert len(a.attempts) == 2 and a.attempts[0].error
    fix_msg = llm.calls[1][1][-1]["content"]
    assert "nme" in fix_msg.lower()  # the database error text reached the model


def test_unsafe_sql_never_executes(executor):
    evil = "```sql\nDELETE FROM orders\n```\nOops."
    a = NQL(executor, FakeLLM(evil, evil, evil)).ask("delete everything")
    assert a.result is None and "Only SELECT" in a.error
    assert executor.run("SELECT COUNT(*) FROM orders").rows == [[14]]


def test_read_only_transaction_blocks_writes_even_if_guard_is_bypassed(executor):
    with pytest.raises(QueryError):
        executor._execute("DELETE FROM orders", fetch=False)
    assert executor.run("SELECT COUNT(*) FROM orders").rows == [[14]]


def test_like_with_percent_signs(executor):
    rows = executor.run("SELECT name FROM customers WHERE name LIKE '%Kim%'").rows
    assert rows == [["Grace Kim"]]


def test_explain_mode_validates_without_running(executor):
    a = NQL(executor, FakeLLM(GOOD)).ask("orders per customer", execute=False)
    assert a.error is None and a.result is None
    with pytest.raises(QueryError):
        executor.explain("SELECT missing_col FROM customers")
    with pytest.raises(UnsafeSQL):
        executor.explain("DROP TABLE customers")


def test_cannot_answer_is_reported():
    assert parse_reply("CANNOT_ANSWER: no weather data") == (None, "no weather data")
    assert parse_reply("SELECT 1") == ("SELECT 1", "")


def test_large_schema_uses_table_selection(executor, engine):
    llm = FakeLLM('["orders"]', GOOD)
    a = NQL(executor, llm, max_tables_in_prompt=2).ask("orders per customer")
    # orders + its FK neighbours (customers, order_items), but not products
    assert set(a.tables_used) == {t(n, engine) for n in ("orders", "customers", "order_items")}
    assert f"TABLE {t('products', engine)}\n" not in llm.calls[1][0]


def test_history_is_sent_for_follow_ups(executor):
    llm = FakeLLM(GOOD)
    NQL(executor, llm).ask("only India", history=[{"question": "orders per customer",
                                                   "sql": "SELECT 1"}])
    msgs = llm.calls[0][1]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
