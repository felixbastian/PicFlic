import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.bot import (
    format_multirow_query_response,
    format_query_response,
    format_result_response,
    handle_message,
    persist_result,
    resolve_user_id,
    start,
)
from src.logging_context import clear_log_context, get_log_context
from src.models import ExpenseAnalysis, NutritionAnalysis


@pytest.fixture(autouse=True)
def _clear_logging_context_between_tests():
    clear_log_context()
    yield
    clear_log_context()


class _FakeFile:
    async def download_to_drive(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(b"image-bytes")


class _FakePhoto:
    async def get_file(self) -> _FakeFile:
        return _FakeFile()


class _FakeMessage:
    def __init__(self) -> None:
        self.photo = [_FakePhoto()]
        self.text = None
        self.caption = None
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeAgent:
    def __init__(
        self,
        image_result: dict | None = None,
        text_result: dict | None = None,
    ) -> None:
        self.image_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.image_result = image_result or {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "analysis": {
                "category": "drink",
                "calories": 151.7,
                "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
                "tags": ["alcoholic"],
                "alcohol_units": 1.5,
            },
        }
        self.text_result = text_result or {"workflow_type": "echo"}

    def process_image(self, image_path: str, metadata: dict | None = None) -> dict:
        self.image_calls.append(
            {"image_path": image_path, "metadata": metadata, "log_context": get_log_context()}
        )
        return self.image_result

    def process_text(self, text: str, metadata: dict | None = None) -> dict:
        self.text_calls.append({"text": text, "metadata": metadata, "log_context": get_log_context()})
        return self.text_result


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []
        self.consumption_calls: list[dict] = []
        self.expense_calls: list[dict] = []
        self.daily_calories_calls: list[str] = []
        self.query_calls: list[dict] = []
        self.query_result = [
            {
                "result_value": Decimal("42.50"),
                "result_unit": "EUR",
                "result_label": "Lebensmitteleinkäufe",
                "period_label": "January 2026",
            }
        ]

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"

    async def store_consumption(self, user_id: str, analysis: NutritionAnalysis) -> str:
        self.consumption_calls.append({"user_id": user_id, "analysis": analysis})
        return "meal-123"

    async def store_expense(self, user_id: str, analysis: ExpenseAnalysis) -> str:
        self.expense_calls.append({"user_id": user_id, "analysis": analysis})
        return "expense-123"

    async def get_daily_calories(self, user_id: str) -> int:
        self.daily_calories_calls.append(user_id)
        return 1800

    async def execute_guarded_query(
        self, query: str, user_id: str, allowed_tables: tuple[str, ...]
    ) -> list[dict]:
        self.query_calls.append(
            {"query": query, "user_id": user_id, "allowed_tables": allowed_tables}
        )
        return self.query_result


def test_start_replies_with_welcome_message():
    message = _FakeMessage()
    update = SimpleNamespace(message=message)

    asyncio.run(start(update, SimpleNamespace()))

    assert message.replies == [
        "Hi! Send me a photo of your food or a receipt, or ask about your tracked expenses and nutrition."
    ]


def test_handle_message_stores_fact_consumption():
    message = _FakeMessage()
    agent = _FakeAgent()
    update = SimpleNamespace(
        update_id=1001,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(agent.image_calls) == 1
    assert agent.image_calls[0]["metadata"] == {}
    assert agent.image_calls[0]["log_context"]["process_id"].startswith("telegram-")
    assert agent.image_calls[0]["log_context"]["telegram_user_id"] == "42"
    assert agent.image_calls[0]["log_context"]["update_id"] == "1001"
    assert postgres_db.user_calls == [
        {
            "telegram_user_id": 42,
            "username": "felix",
            "first_name": "Felix",
            "last_name": "Hans",
        }
    ]
    assert len(postgres_db.consumption_calls) == 1
    assert postgres_db.consumption_calls[0]["user_id"] == "user-123"
    analysis = postgres_db.consumption_calls[0]["analysis"]
    assert analysis.category == "drink"
    assert analysis.calories == 151.7
    assert analysis.tags == ["alcoholic"]
    assert postgres_db.daily_calories_calls == ["user-123"]
    assert message.replies == [
        "Category: drink\nCalories: 151.7\nTags: alcoholic\nToday's total calories: 1800"
    ]


def test_handle_message_stores_fact_expense():
    message = _FakeMessage()
    agent = _FakeAgent(
        image_result={
            "task_type": "expense",
            "record_id": "expense-123",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        }
    )
    update = SimpleNamespace(
        update_id=1010,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(postgres_db.expense_calls) == 1
    assert postgres_db.expense_calls[0]["user_id"] == "user-123"
    assert postgres_db.expense_calls[0]["analysis"].category == "Lebensmitteleinkäufe"
    assert postgres_db.daily_calories_calls == []
    assert message.replies == [
        "Expense added to the database.\n"
        "Total: EUR 43.20\n"
        "Category: Lebensmitteleinkäufe\n"
        "Description: Groceries and toiletries"
    ]


def test_handle_message_reuses_webhook_resolved_user_id():
    message = _FakeMessage()
    agent = _FakeAgent()
    update = SimpleNamespace(
        update_id=1002,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"_picflic_user_ids": {1002: "user-from-webhook"}})
    )
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.user_calls == []
    assert len(postgres_db.consumption_calls) == 1
    assert postgres_db.consumption_calls[0]["user_id"] == "user-from-webhook"
    assert postgres_db.daily_calories_calls == ["user-from-webhook"]
    assert context.application.bot_data["_picflic_user_ids"] == {}


def test_handle_message_passes_caption_as_user_prompt():
    message = _FakeMessage()
    message.caption = "This is a chicken salad with extra avocado"
    agent = _FakeAgent()
    update = SimpleNamespace(
        update_id=1005,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={})), agent))

    assert len(agent.image_calls) == 1
    assert agent.image_calls[0]["metadata"] == {
        "user_prompt": "This is a chicken salad with extra avocado"
    }
    assert message.replies == ["Category: drink\nCalories: 151.7\nTags: alcoholic"]


def test_handle_message_runs_expense_text_query():
    message = _FakeMessage()
    message.photo = []
    message.text = "What are the total expenses in January on groceries?"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "expense_query",
            "explanation": 'I am looking for all expenses in the category "Lebensmitteleinkäufe" for January 2026.',
            "sql_query": (
                "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value, "
                "'EUR' AS result_unit, 'Lebensmitteleinkäufe' AS result_label, "
                "'January 2026' AS period_label "
                "FROM fact_expenses WHERE user_id = $1"
            ),
            "response_template": (
                "You spent a total of {result_value} {result_unit} on {result_label} in {period_label}."
            ),
        }
    )
    update = SimpleNamespace(
        update_id=1012,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["text"] == message.text
    assert agent.text_calls[0]["metadata"] is None
    assert agent.text_calls[0]["log_context"]["process_id"].startswith("telegram-")
    assert agent.text_calls[0]["log_context"]["telegram_user_id"] == "42"
    assert agent.text_calls[0]["log_context"]["update_id"] == "1012"
    assert agent.text_calls[0]["log_context"]["action"] == "telegram_message"
    assert postgres_db.query_calls == [
        {
            "query": (
                "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value, "
                "'EUR' AS result_unit, 'Lebensmitteleinkäufe' AS result_label, "
                "'January 2026' AS period_label "
                "FROM fact_expenses WHERE user_id = $1"
            ),
            "user_id": "user-123",
            "allowed_tables": ("fact_expenses",),
        }
    ]
    assert message.replies == [
        'I am looking for all expenses in the category "Lebensmitteleinkäufe" for January 2026.',
        "You spent a total of 42.50 EUR on Lebensmitteleinkäufe in January 2026.",
    ]


def test_handle_message_runs_nutrition_text_query():
    message = _FakeMessage()
    message.photo = []
    message.text = "How many calories have I consumed this month?"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "nutrition_query",
            "explanation": "I am looking for all tracked calories in March 2026.",
            "sql_query": (
                "SELECT COALESCE(SUM(calories), 0) AS result_value, "
                "'kcal' AS result_unit, 'calories' AS result_label, "
                "'March 2026' AS period_label "
                "FROM fact_consumption WHERE user_id = $1"
            ),
            "response_template": (
                "You have consumed {result_value} {result_unit} of {result_label} in {period_label}."
            ),
        }
    )
    update = SimpleNamespace(
        update_id=1013,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    postgres_db = _FakePostgresDatabase()
    postgres_db.query_result = [
        {
            "result_value": 1800,
            "result_unit": "kcal",
            "result_label": "calories",
            "period_label": "March 2026",
        }
    ]

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.query_calls[0]["allowed_tables"] == ("fact_consumption",)
    assert message.replies == [
        "I am looking for all tracked calories in March 2026.",
        "You have consumed 1800 kcal of calories in March 2026.",
    ]


def test_handle_message_formats_multirow_query_results():
    message = _FakeMessage()
    message.photo = []
    message.text = "Wieviel habe ich diesen Monat in den verschiedenen Kategorien ausgegeben?"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "expense_query",
            "explanation": "I am looking for total expenses by category for this month (March 2026).",
            "sql_query": (
                "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value, "
                "'EUR' AS result_unit, category AS result_label, "
                "'March 2026' AS period_label "
                "FROM fact_expenses WHERE user_id = $1 GROUP BY category ORDER BY result_value DESC"
            ),
            "response_template": (
                "For {period_label}, you spent {result_value} {result_unit} in {result_label}."
            ),
        }
    )
    update = SimpleNamespace(
        update_id=1015,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    postgres_db = _FakePostgresDatabase()
    postgres_db.query_result = [
        {
            "result_value": Decimal("145.47"),
            "result_unit": "EUR",
            "result_label": "Kleidung",
            "period_label": "March 2026",
        },
        {
            "result_value": Decimal("84.50"),
            "result_unit": "EUR",
            "result_label": "Lebensmitteleinkäufe",
            "period_label": "March 2026",
        },
        {
            "result_value": Decimal("25.00"),
            "result_unit": "EUR",
            "result_label": "Bäcker",
            "period_label": "March 2026",
        },
    ]

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert message.replies == [
        "I am looking for total expenses by category for this month (March 2026).",
        "Breakdown for March 2026:\nKleidung: 145.47 EUR\nLebensmitteleinkäufe: 84.50 EUR\nBäcker: 25 EUR",
    ]


def test_handle_message_echoes_plain_text_when_orchestrator_says_echo():
    message = _FakeMessage()
    message.photo = []
    message.text = "hello there"
    agent = _FakeAgent(text_result={"workflow_type": "echo"})
    update = SimpleNamespace(
        update_id=1006,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={})), agent))

    assert agent.image_calls == []
    assert agent.text_calls[0]["text"] == "hello there"
    assert agent.text_calls[0]["metadata"] is None
    assert agent.text_calls[0]["log_context"]["process_id"].startswith("telegram-")
    assert message.replies == ["hello there"]


def test_handle_message_reports_query_unavailable_without_postgres():
    message = _FakeMessage()
    message.photo = []
    message.text = "What are my current expenses this month?"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "expense_query",
            "explanation": "I am looking for all expenses in March 2026.",
            "sql_query": "SELECT 1 AS result_value FROM fact_expenses WHERE user_id = $1",
            "response_template": "The total is {result_value}.",
        }
    )
    update = SimpleNamespace(
        update_id=1014,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={})), agent))

    assert message.replies == ["Database-backed questions are not available right now."]


def test_resolve_user_id_uses_pending_mapping():
    postgres_db = _FakePostgresDatabase()
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"_picflic_user_ids": {1009: "user-from-webhook"}}))
    update = SimpleNamespace(
        update_id=1009,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
    )

    async def _resolve():
        user_id = await resolve_user_id(update, context, postgres_db)
        return user_id, get_log_context()

    user_id, log_context = asyncio.run(_resolve())

    assert user_id == "user-from-webhook"
    assert log_context["user_id"] == "user-from-webhook"
    assert postgres_db.user_calls == []
    assert context.application.bot_data["_picflic_user_ids"] == {}


def test_persist_result_creates_nutrition_entry_when_needed():
    postgres_db = _FakePostgresDatabase()
    update = SimpleNamespace(
        update_id=1003,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    result = {
        "task_type": "nutrition",
        "analysis": {
            "category": "drink",
            "calories": 151.7,
            "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
            "tags": ["alcoholic"],
            "alcohol_units": 1.5,
        },
    }

    note = asyncio.run(persist_result(update, context, postgres_db, result))

    assert note == "Today's total calories: 1800"
    assert len(postgres_db.user_calls) == 1
    assert len(postgres_db.consumption_calls) == 1
    assert len(postgres_db.expense_calls) == 0


def test_persist_result_creates_expense_entry():
    postgres_db = _FakePostgresDatabase()
    update = SimpleNamespace(
        update_id=1011,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    result = {
        "task_type": "expense",
        "analysis": {
            "description": "Groceries and toiletries",
            "expense_total_amount_in_euros": 43.2,
            "category": "Lebensmitteleinkäufe",
        },
    }

    note = asyncio.run(persist_result(update, context, postgres_db, result))

    assert note == "Expense added to the database."
    assert len(postgres_db.user_calls) == 1
    assert len(postgres_db.expense_calls) == 1
    assert len(postgres_db.consumption_calls) == 0


def test_persist_result_requires_effective_user():
    with pytest.raises(ValueError, match="effective Telegram user"):
        asyncio.run(
            persist_result(
                SimpleNamespace(update_id=1004, effective_user=None),
                SimpleNamespace(application=SimpleNamespace(bot_data={})),
                _FakePostgresDatabase(),
                {
                    "task_type": "nutrition",
                    "analysis": {
                        "category": "drink",
                        "calories": 151.7,
                        "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
                        "tags": ["alcoholic"],
                        "alcohol_units": 1.5,
                    },
                },
            )
        )


def test_format_result_response_formats_expense():
    response = format_result_response(
        {
            "task_type": "expense",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        },
        "Expense added to the database.",
    )

    assert response == (
        "Expense added to the database.\n"
        "Total: EUR 43.20\n"
        "Category: Lebensmitteleinkäufe\n"
        "Description: Groceries and toiletries"
    )


def test_format_query_response_uses_safe_template_fields():
    response = format_query_response(
        {
            "response_template": (
                "You spent a total of {result_value} {result_unit} on {result_label} in {period_label}."
            )
        },
        {
            "result_value": Decimal("42.50"),
            "result_unit": "EUR",
            "result_label": "Lebensmitteleinkäufe",
            "period_label": "January 2026",
        },
    )

    assert response == "You spent a total of 42.50 EUR on Lebensmitteleinkäufe in January 2026."


def test_format_multirow_query_response_formats_compact_breakdown():
    response = format_multirow_query_response(
        {},
        [
            {
                "result_value": Decimal("145.47"),
                "result_unit": "EUR",
                "result_label": "Kleidung",
                "period_label": "March 2026",
            },
            {
                "result_value": Decimal("84.50"),
                "result_unit": "EUR",
                "result_label": "Lebensmitteleinkäufe",
                "period_label": "March 2026",
            },
        ],
    )

    assert response == (
        "Breakdown for March 2026:\n"
        "Kleidung: 145.47 EUR\n"
        "Lebensmitteleinkäufe: 84.50 EUR"
    )
