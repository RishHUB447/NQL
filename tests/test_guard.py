import pytest

from nql.guard import UnsafeSQL, check_read_only, with_row_limit

ALLOWED = [
    "SELECT 1",
    "select * from orders where status = 'delivered';",
    "WITH t AS (SELECT customer_id, COUNT(*) n FROM orders GROUP BY 1) SELECT * FROM t",
    "SELECT id FROM customers UNION SELECT id FROM orders",
    "SELECT name FROM customers WHERE name LIKE '%a%'",
    "SELECT 'DROP TABLE x' AS harmless_text",
]

BLOCKED = [
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders VALUES (1)",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "ALTER TABLE orders ADD COLUMN x int",
    "CREATE TABLE x AS SELECT 1",
    "SELECT 1; DROP TABLE orders",
    "SELECT * INTO backup FROM orders",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "SELECT pg_sleep(100)",
    "SELECT * FROM orders FOR UPDATE",
    "GRANT ALL ON orders TO bob",
    "SET search_path = evil",
    "",
]


@pytest.mark.parametrize("sql", ALLOWED)
@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_allows_read_queries(sql, dialect):
    check_read_only(sql, dialect)


@pytest.mark.parametrize("sql", BLOCKED)
def test_blocks_writes_and_tricks(sql):
    with pytest.raises(UnsafeSQL):
        check_read_only(sql, "postgresql")


@pytest.mark.parametrize("sql", ["SELECT SLEEP(10)", "SELECT LOAD_FILE('/etc/passwd')",
                                 "SELECT BENCHMARK(1000000, MD5('a'))"])
def test_blocks_mysql_specific_functions(sql):
    with pytest.raises(UnsafeSQL):
        check_read_only(sql, "mysql")


def test_row_limit_added_only_when_missing():
    sql = "SELECT * FROM orders -- all of them"
    assert with_row_limit(sql, check_read_only(sql, "postgresql"), 10).endswith("\nLIMIT 10")
    sql = "SELECT * FROM orders LIMIT 3;"
    assert with_row_limit(sql, check_read_only(sql, "postgresql"), 10) == "SELECT * FROM orders LIMIT 3"
