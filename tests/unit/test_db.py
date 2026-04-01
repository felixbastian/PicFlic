import asyncio
import uuid
from datetime import datetime

import pytest

from src.db import PostgresDatabase, validate_readonly_query
from src.models import ExpenseAnalysis, MacroBreakdown, NutritionAnalysis, RecipeAnalysis
from src.vocabulary_review import build_review_prompt_text


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_result = None
        self.fetch_result = []
        self.fetchrow_results = []

    class _Transaction:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def execute(self, query: str, *args) -> None:
        self.execute_calls.append((query, args))

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return self.fetch_result

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    def transaction(self):
        return self._Transaction()


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
        ingredients=[
            {"name": "pizza dough", "amount": "180 g", "calories": 320.0},
            {"name": "cheese", "amount": "80 g", "calories": 192.4},
        ],
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


def test_has_vocab_bot_activated_reads_flag():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetchval_result = True

    activated = asyncio.run(db.has_vocab_bot_activated("user-123"))

    assert activated is True
    calls = db._pool.connection.fetchval_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "has_vocab_bot_activated" in query
    assert params == ("user-123",)


def test_get_recent_prompted_vocabulary_review_by_prompt_matches_prompt_text():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetch_result = [
        {
            "vocabulary_id": "vocab-1",
            "user_id": "user-123",
            "telegram_user_id": 42,
            "french_word": "aller",
            "english_description": "to go",
        }
    ]

    result = asyncio.run(
        db.get_recent_prompted_vocabulary_review_by_prompt(
            42,
            build_review_prompt_text("to go"),
        )
    )

    assert result is not None
    assert result.vocabulary_id == "vocab-1"
    calls = db._pool.connection.fetch_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "last_review_prompted_at IS NOT NULL" in query
    assert params == (42, 25)


def test_get_recent_prompted_vocabulary_review_by_french_word_matches_recent_word():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetch_result = [
        {
            "vocabulary_id": "vocab-1",
            "user_id": "user-123",
            "telegram_user_id": 42,
            "french_word": "accru, accroître",
            "english_description": "to increase",
        }
    ]

    result = asyncio.run(
        db.get_recent_prompted_vocabulary_review_by_french_word(
            42,
            "accru, accroître",
        )
    )

    assert result is not None
    assert result.vocabulary_id == "vocab-1"
    calls = db._pool.connection.fetch_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "last_review_prompted_at IS NOT NULL" in query
    assert params == (42, 25)


def test_list_stale_vocabulary_review_reminders_reads_pending_rows_ready_for_resend():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetch_result = [
        {
            "vocabulary_id": "vocab-1",
            "user_id": "user-123",
            "telegram_user_id": 42,
            "french_word": "aller",
            "english_description": "to go",
            "current_review_stage": "day",
            "next_review_at": datetime(2026, 3, 23, 10, 0, 0),
        }
    ]

    result = asyncio.run(db.list_stale_vocabulary_review_reminders())

    assert len(result) == 1
    assert result[0].vocabulary_id == "vocab-1"
    calls = db._pool.connection.fetch_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "v.awaiting_review = TRUE" in query
    assert "v.last_review_prompted_at IS NULL" in query
    assert params[1] == 100


def test_update_consumption_updates_fact_row():
    db = PostgresDatabase()
    db._pool = _FakePool()
    analysis = NutritionAnalysis(
        ingredients=[
            {"name": "beer", "amount": "330 ml", "calories": 110.0},
        ],
        category="drink",
        calories=110.0,
        macros=MacroBreakdown(carbs=9.0, protein=1.0, fat=0.0),
        tags=["alcoholic"],
        alcohol_units=1.0,
    )

    asyncio.run(db.update_consumption("meal-123", "user-123", analysis))

    calls = db._pool.connection.execute_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "UPDATE fact_consumption" in query
    assert params == ("meal-123", "user-123", "drink", 110, ["alcoholic"], 1.0)


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


def test_store_vocabulary_inserts_fact_row():
    db = PostgresDatabase()
    db._pool = _FakePool()

    vocabulary_id = asyncio.run(
        db.store_vocabulary(
            "user-123",
            "bonjour",
            "hello; a common French greeting used when meeting someone.",
        )
    )

    assert vocabulary_id
    calls = db._pool.connection.execute_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "INSERT INTO fact_vocabulary" in query
    assert params[0] == vocabulary_id
    assert params[1] == "user-123"
    assert params[2] == "bonjour"
    assert params[3] == "hello; a common French greeting used when meeting someone."


def test_store_dish_inserts_fact_row():
    db = PostgresDatabase()
    db._pool = _FakePool()
    analysis = RecipeAnalysis(
        name="Lemon pasta",
        description="Pasta with lemon, butter, and parmesan.",
        carb_source="noodles",
        vegetarian=True,
        meat=None,
        frequency_rotation="monthly",
    )

    dish_id = asyncio.run(db.store_dish("user-123", analysis))

    assert dish_id
    calls = db._pool.connection.execute_calls
    assert len(calls) == 1
    query, params = calls[0]
    assert "INSERT INTO fact_dishes" in query
    assert params[0] == dish_id
    assert params[1] == "user-123"
    assert params[2] is None
    assert params[3] == "Lemon pasta"
    assert params[4] == "Pasta with lemon, butter, and parmesan."
    assert params[5] == "noodles"
    assert params[6] is True
    assert params[7] is None
    assert params[8] == "monthly"


def test_list_due_vocabulary_reviews_returns_due_rows():
    db = PostgresDatabase()
    db._pool = _FakePool()
    vocabulary_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db._pool.connection.fetch_result = [
        {
            "vocabulary_id": vocabulary_id,
            "user_id": user_id,
            "telegram_user_id": 42,
            "french_word": "bonjour",
            "english_description": "hello; a common French greeting.",
            "current_review_stage": "day",
            "next_review_at": datetime(2026, 3, 23, 10, 0, 0),
        }
    ]

    rows = asyncio.run(db.list_due_vocabulary_reviews())

    assert len(rows) == 1
    assert rows[0].vocabulary_id == str(vocabulary_id)
    assert rows[0].user_id == str(user_id)
    assert rows[0].telegram_user_id == 42


def test_get_pending_vocabulary_review_normalizes_uuid_rows():
    db = PostgresDatabase()
    db._pool = _FakePool()
    vocabulary_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db._pool.connection.fetchrow_results = [
        {
            "vocabulary_id": vocabulary_id,
            "user_id": user_id,
            "telegram_user_id": 42,
            "french_word": "bonjour",
            "english_description": "hello; a common French greeting.",
            "current_review_stage": "day",
            "next_review_at": datetime(2026, 3, 23, 10, 0, 0),
        }
    ]

    row = asyncio.run(db.get_pending_vocabulary_review(42))

    assert row is not None
    assert row.vocabulary_id == str(vocabulary_id)
    assert row.user_id == str(user_id)
    assert row.telegram_user_id == 42


def test_get_next_due_vocabulary_review_for_user_returns_due_row():
    db = PostgresDatabase()
    db._pool = _FakePool()
    vocabulary_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db._pool.connection.fetchrow_results = [
        {
            "vocabulary_id": vocabulary_id,
            "user_id": user_id,
            "telegram_user_id": 42,
            "french_word": "fromage",
            "english_description": "cheese",
            "current_review_stage": "week",
            "next_review_at": datetime(2026, 3, 23, 10, 5, 0),
        }
    ]

    row = asyncio.run(db.get_next_due_vocabulary_review_for_user(str(user_id)))

    assert row is not None
    assert row.vocabulary_id == str(vocabulary_id)
    assert row.user_id == str(user_id)
    assert row.french_word == "fromage"


def test_record_vocabulary_review_result_advances_stage_on_correct_answer():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetchrow_results = [
        {
            "vocabulary_id": "vocab-1",
            "user_id": "user-123",
            "french_word": "bonjour",
            "current_review_stage": "day",
        },
        {
            "next_review_at": datetime(2026, 3, 26, 10, 0, 0),
            "current_review_stage": "three_days",
        },
    ]

    result = asyncio.run(db.record_vocabulary_review_result("vocab-1", correct=True))

    assert result.correct is True
    assert result.current_review_stage == "three_days"
    query, params = db._pool.connection.execute_calls[0]
    assert "correct_day = TRUE" in query
    assert "current_review_stage = 'three_days'" in query
    assert params == ("vocab-1",)


def test_record_vocabulary_review_result_shelves_word():
    db = PostgresDatabase()
    db._pool = _FakePool()
    db._pool.connection.fetchrow_results = [
        {
            "vocabulary_id": "vocab-2",
            "user_id": "user-123",
            "french_word": "fromage",
            "current_review_stage": "week",
        }
    ]

    result = asyncio.run(db.record_vocabulary_review_result("vocab-2", shelved=True))

    assert result.shelved is True
    query, params = db._pool.connection.execute_calls[0]
    assert "shelf = TRUE" in query
    assert params == ("vocab-2",)


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
