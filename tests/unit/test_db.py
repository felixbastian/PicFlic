import asyncio

from src.db import PostgresDatabase
from src.models import ImageAnalysis, MacroBreakdown


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetchval_result = None

    async def execute(self, query: str, *args) -> None:
        self.execute_calls.append((query, args))

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result


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
    analysis = ImageAnalysis(
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
