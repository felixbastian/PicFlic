import asyncio

import pytest

from src.db import PostgresDatabase, validate_readonly_query
from src.models import ExpenseAnalysis, MacroBreakdown, NutritionAnalysis


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_result = None
        self.fetch_result = []

    async def execute(self, query: str, *args) -> None:
        self.execute_calls.append((query, args))

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return self.fetch_result


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)


def test_store_consumption_inserts_fact_row():
    db = PostgresDatabase()
    db._pool = _FakePool()
    analysis = NutritionAnalysis(
        category="food",
        calories=512.4,
        macros=MacroBreakdown(carbs=40.0, protein=22.0, fat=19.0),
        tags=["meal", "pizza"],
        alcohol_units=0.0,
    )

    meal_id = asyncio.run(db.store_consumption("user-123", analysis))

    assert meal_id
    calls = db._pool.connection.execute_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "INSERT INTO fact_consumption" in query
    assert params[0] == meal_id
    assert params[1] == "user-123"
    assert params[2] == "food"
    assert params[3] == 512
    assert params[4] == ["meal", "pizza"]
    assert params[5] == 0.0


def test_get_daily_calories_sums_today_for_user():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetchval_result = 1800

    total = asyncio.run(db.get_daily_calories("user-123"))

    assert total == 1800
    calls = db._pool.connection.fetchval_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "SUM(calories)" in query
    assert "CURRENT_DATE" in query
    assert params == ("user-123",)


def test_store_expense_inserts_fact_row():
    db = PostgresDatabase()
    db._pool = _FakePool()
    analysis = ExpenseAnalysis(
        description="Groceries and toiletries",
        expense_total_amount_in_euros=43.20,
        category="Lebensmitteleinkäufe",
    )

    expense_id = asyncio.run(db.store_expense("user-123", analysis))

    assert expense_id
    calls = db._pool.connection.execute_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "INSERT INTO fact_expenses" in query
    assert params[0] == expense_id
    assert params[1] == "user-123"
    assert params[2] == "Groceries and toiletries"
    assert params[3] == 43.20
    assert params[4] == "Lebensmitteleinkäufe"


def test_validate_readonly_query_accepts_safe_select():
    query = validate_readonly_query(
        (
            "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value "
            "FROM fact_expenses WHERE user_id = $1"
        ),
        ("fact_expenses",),
    )

    assert query.startswith("SELECT")
    assert "fact_expenses" in query


def test_validate_readonly_query_rejects_destructive_sql():
    with pytest.raises(ValueError, match="Only read-only SELECT queries are allowed"):
        validate_readonly_query("DROP TABLE fact_expenses", ("fact_expenses",))


def test_validate_readonly_query_rejects_disallowed_tables():
    with pytest.raises(ValueError, match="disallowed tables"):
        validate_readonly_query(
            "SELECT * FROM dim_user WHERE user_id = $1",
            ("fact_expenses",),
        )


def test_execute_guarded_query_returns_all_rows():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetch_result = [
        {
            "result_value": 120.5,
            "result_unit": "EUR",
            "result_label": "Lebensmitteleinkäufe",
            "period_label": "January 2026",
        },
        {
            "result_value": 42.0,
            "result_unit": "EUR",
            "result_label": "Kleidung",
            "period_label": "January 2026",
        },
    ]

    rows = asyncio.run(
        db.execute_guarded_query(
            (
                "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value, "
                "'EUR' AS result_unit, category AS result_label, "
                "'January 2026' AS period_label "
                "FROM fact_expenses WHERE user_id = $1 GROUP BY category"
            ),
            "user-123",
            ("fact_expenses",),
        )
    )

    assert len(rows) == 2
    assert rows[0]["result_value"] == 120.5
    calls = db._pool.connection.fetch_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "fact_expenses" in query
    assert params == ("user-123",)
