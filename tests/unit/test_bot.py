import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from telegram.constants import ParseMode

from src.bot import (
    format_multirow_query_response,
    format_query_response,
    format_result_response,
    format_vocabulary_response,
    get_latest_expense_result,
    get_latest_nutrition_result,
    get_latest_tracking_result,
    get_recent_history,
    handle_message,
    persist_result,
    remember_latest_expense_result,
    remember_latest_nutrition_result,
    remember_latest_tracking_result,
    remember_text_turn,
    resolve_user_id,
    start,
)
from src.config import AppConfig
from src.logging_context import clear_log_context, get_log_context
from src.models import (
    DueVocabularyReview,
    ExpenseAnalysis,
    NutritionAnalysis,
    RecipeAnalysis,
    VocabularyReviewResult,
)


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
        self.reply_kwargs: list[dict] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)


class _FakeAgent:
    def __init__(
        self,
        image_result: dict | None = None,
        text_result: dict | None = None,
    ) -> None:
        self.image_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.updated_expense_records: list[dict] = []
        self.updated_nutrition_records: list[dict] = []
        self.deleted_records: list[str] = []
        self.image_result = image_result or {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "analysis": {
                "ingredients": [
                    {"name": "beer", "amount": "500 ml", "calories": 151.7},
                ],
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

    def update_nutrition_record(self, record_id: str, analysis: NutritionAnalysis | dict) -> None:
        self.updated_nutrition_records.append({"record_id": record_id, "analysis": analysis})

    def update_expense_record(self, record_id: str, analysis: ExpenseAnalysis | dict) -> None:
        self.updated_expense_records.append({"record_id": record_id, "analysis": analysis})

    def delete_record(self, record_id: str) -> None:
        self.deleted_records.append(record_id)


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []
        self.consumption_calls: list[dict] = []
        self.expense_calls: list[dict] = []
        self.dish_calls: list[dict] = []
        self.vocabulary_calls: list[dict] = []
        self.daily_calories_calls: list[str] = []
        self.updated_expense_calls: list[dict] = []
        self.updated_consumption_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.deleted_consumption_calls: list[dict] = []
        self.deleted_expense_calls: list[dict] = []
        self.deleted_dish_calls: list[dict] = []
        self.mark_prompted_calls: list[str] = []
        self.record_review_calls: list[dict] = []
        self.vocab_bot_activated = True
        self.query_result = [
            {
                "result_value": Decimal("42.50"),
                "result_unit": "EUR",
                "result_label": "Lebensmitteleinkäufe",
                "period_label": "January 2026",
            }
        ]
        self.due_reviews: list[DueVocabularyReview] = []
        self.pending_review: DueVocabularyReview | None = None

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"

    async def store_consumption(
        self,
        user_id: str,
        analysis: NutritionAnalysis,
        meal_id: str | None = None,
    ) -> str:
        resolved_meal_id = meal_id or "meal-123"
        self.consumption_calls.append(
            {"user_id": user_id, "analysis": analysis, "meal_id": resolved_meal_id}
        )
        return resolved_meal_id

    async def store_expense(self, user_id: str, analysis: ExpenseAnalysis) -> str:
        self.expense_calls.append({"user_id": user_id, "analysis": analysis})
        return "expense-123"

    async def store_dish(self, user_id: str, analysis: RecipeAnalysis) -> str:
        self.dish_calls.append({"user_id": user_id, "analysis": analysis})
        return "dish-123"

    async def store_vocabulary(self, user_id: str, french_word: str, english_description: str) -> str:
        self.vocabulary_calls.append(
            {
                "user_id": user_id,
                "french_word": french_word,
                "english_description": english_description,
            }
        )
        return "vocabulary-123"

    async def has_vocab_bot_activated(self, user_id: str) -> bool:
        return self.vocab_bot_activated

    async def get_daily_calories(self, user_id: str) -> int:
        self.daily_calories_calls.append(user_id)
        return 1800

    async def update_consumption(self, meal_id: str, user_id: str, analysis: NutritionAnalysis | dict) -> None:
        self.updated_consumption_calls.append(
            {"meal_id": meal_id, "user_id": user_id, "analysis": analysis}
        )

    async def update_expense(self, expense_id: str, user_id: str, analysis: ExpenseAnalysis | dict) -> None:
        self.updated_expense_calls.append(
            {"expense_id": expense_id, "user_id": user_id, "analysis": analysis}
        )

    async def delete_consumption(self, meal_id: str, user_id: str) -> None:
        self.deleted_consumption_calls.append({"meal_id": meal_id, "user_id": user_id})

    async def delete_expense(self, expense_id: str, user_id: str) -> None:
        self.deleted_expense_calls.append({"expense_id": expense_id, "user_id": user_id})

    async def delete_dish(self, dish_id: str, user_id: str) -> None:
        self.deleted_dish_calls.append({"dish_id": dish_id, "user_id": user_id})

    async def execute_guarded_query(
        self, query: str, user_id: str, allowed_tables: tuple[str, ...]
    ) -> list[dict]:
        self.query_calls.append(
            {"query": query, "user_id": user_id, "allowed_tables": allowed_tables}
        )
        return self.query_result

    async def list_due_vocabulary_reviews(self, limit: int = 100) -> list[DueVocabularyReview]:
        return self.due_reviews[:limit]

    async def mark_vocabulary_review_prompted(self, vocabulary_id: str) -> None:
        self.mark_prompted_calls.append(vocabulary_id)

    async def get_pending_vocabulary_review(self, telegram_user_id: int) -> DueVocabularyReview | None:
        return self.pending_review

    async def get_next_due_vocabulary_review_for_user(self, user_id: str) -> DueVocabularyReview | None:
        for review in self.due_reviews:
            if review.user_id == user_id:
                return review
        return None

    async def record_vocabulary_review_result(
        self,
        vocabulary_id: str,
        *,
        correct: bool = False,
        shelved: bool = False,
    ) -> VocabularyReviewResult:
        self.record_review_calls.append(
            {"vocabulary_id": vocabulary_id, "correct": correct, "shelved": shelved}
        )
        if self.pending_review is None:
            raise AssertionError("No pending review configured")
        if shelved:
            return VocabularyReviewResult(
                vocabulary_id=vocabulary_id,
                user_id=self.pending_review.user_id,
                french_word=self.pending_review.french_word,
                correct=False,
                shelved=True,
                finished=False,
                current_review_stage=None,
                next_review_at=None,
            )
        if correct:
            return VocabularyReviewResult(
                vocabulary_id=vocabulary_id,
                user_id=self.pending_review.user_id,
                french_word=self.pending_review.french_word,
                correct=True,
                shelved=False,
                finished=False,
                current_review_stage="three_days",
                next_review_at=None,
            )
        return VocabularyReviewResult(
            vocabulary_id=vocabulary_id,
            user_id=self.pending_review.user_id,
            french_word=self.pending_review.french_word,
            correct=False,
            shelved=False,
            finished=False,
            current_review_stage=self.pending_review.current_review_stage,
            next_review_at=None,
        )


class _FakeTelegramBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text})


class _FakeApplication:
    def __init__(self) -> None:
        self.bot = _FakeTelegramBot()


def test_start_replies_with_welcome_message():
    message = _FakeMessage()
    update = SimpleNamespace(message=message)

    asyncio.run(start(update, SimpleNamespace()))

    assert message.replies == [
        "Hi! Send me a photo of your food or a receipt, or just text what you ate or drank, ask about your tracked expenses and nutrition, send me a French word to practice vocabulary, or tell me to save a recipe to your collection."
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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
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
        "<b>Ingredients</b>\n"
        "- Beer : 500 ml (151.7 kcal)\n"
        "\n"
        "<b>Calories:</b> 151.7\n"
        "<b>Tags:</b> alcoholic\n"
        "\n"
        "<b>Today total calories:</b> 1800"
    ]
    assert message.reply_kwargs == [{"parse_mode": ParseMode.HTML}]


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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(postgres_db.expense_calls) == 1
    assert postgres_db.expense_calls[0]["user_id"] == "user-123"
    assert postgres_db.expense_calls[0]["analysis"].category == "Lebensmitteleinkäufe"
    latest_result = get_latest_tracking_result(context)
    assert latest_result is not None
    assert latest_result["task_type"] == "expense"
    assert latest_result["expense_id"] == "expense-123"
    latest_expense = get_latest_expense_result(context)
    assert latest_expense is not None
    assert latest_expense["expense_id"] == "expense-123"
    assert latest_expense["analysis"]["category"] == "Lebensmitteleinkäufe"
    assert postgres_db.daily_calories_calls == []
    assert message.replies == [
        "Expense added to the database.\n"
        "Total: EUR 43.20\n"
        "Category: Lebensmitteleinkäufe\n"
        "Description: Groceries and toiletries"
    ]


def test_handle_message_stores_fact_dish():
    message = _FakeMessage()
    agent = _FakeAgent(
        image_result={
            "task_type": "recipe",
            "record_id": "dish-123",
            "analysis": {
                "name": "Chicken rice bowl",
                "description": "A simple chicken rice bowl with vegetables.",
                "carb_source": "rice",
                "vegetarian": False,
                "meat": "chicken",
                "frequency_rotation": "bi-weekly",
            },
        }
    )
    update = SimpleNamespace(
        update_id=1011,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(postgres_db.dish_calls) == 1
    assert postgres_db.dish_calls[0]["user_id"] == "user-123"
    assert postgres_db.dish_calls[0]["analysis"].name == "Chicken rice bowl"
    latest_result = get_latest_tracking_result(context)
    assert latest_result is not None
    assert latest_result["task_type"] == "recipe"
    assert latest_result["dish_id"] == "dish-123"
    assert message.replies == [
        "Recipe added to your collection.\n"
        "Name: Chicken rice bowl\n"
        "Description: A simple chicken rice bowl with vegetables.\n"
        "Carb source: rice\n"
        "Vegetarian: no\n"
        "Meat: chicken\n"
        "Frequency: bi-weekly"
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

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={}), agent))

    assert len(agent.image_calls) == 1
    assert agent.image_calls[0]["metadata"] == {
        "user_prompt": "This is a chicken salad with extra avocado"
    }
    assert message.replies == [
        "<b>Ingredients</b>\n"
        "- Beer : 500 ml (151.7 kcal)\n"
        "\n"
        "<b>Calories:</b> 151.7\n"
        "<b>Tags:</b> alcoholic"
    ]
    assert message.reply_kwargs == [{"parse_mode": ParseMode.HTML}]


def test_handle_message_applies_nutrition_correction_and_updates_existing_entry():
    agent = _FakeAgent()
    agent.text_result = {
        "workflow_type": "nutrition_correction",
        "task_type": "nutrition",
        "record_id": "meal-123",
        "meal_id": "meal-123",
        "analysis": {
            "ingredients": [
                {"name": "beer", "amount": "330 ml", "calories": 110.0},
            ],
            "category": "drink",
            "calories": 110.0,
            "macros": {"carbs": 9.0, "protein": 1.0, "fat": 0.0},
            "tags": ["alcoholic"],
            "alcohol_units": 1.0,
        },
    }
    postgres_db = _FakePostgresDatabase()
    message = _FakeMessage()
    message.photo = []
    message.text = "It was actually a small beer, only 330 ml"
    update = SimpleNamespace(
        update_id=1016,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )
    remember_latest_tracking_result(
        context,
        {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "analysis": agent.image_result["analysis"],
        },
    )
    remember_latest_nutrition_result(
        context,
        {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "analysis": agent.image_result["analysis"],
        },
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["text"] == message.text
    assert agent.text_calls[0]["metadata"] == {
        "recent_history": [],
        "latest_tracking_result": {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "expense_id": "",
            "dish_id": "",
            "analysis": agent.image_result["analysis"],
        },
        "latest_nutrition_result": {
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "analysis": agent.image_result["analysis"],
        },
    }
    assert agent.updated_nutrition_records[0]["record_id"] == "meal-123"
    assert postgres_db.updated_consumption_calls[0]["meal_id"] == "meal-123"
    assert postgres_db.updated_consumption_calls[0]["user_id"] == "user-123"
    latest_result = get_latest_nutrition_result(context)
    assert latest_result is not None
    assert latest_result["analysis"]["calories"] == 110.0
    assert message.replies == [
        "<b>Ingredients</b>\n"
        "- Beer : 330 ml (110.0 kcal)\n"
        "\n"
        "<b>Calories:</b> 110.0\n"
        "<b>Tags:</b> alcoholic\n"
        "\n"
        "Updated your previous nutrition entry\n"
        "\n"
        "<b>Today total calories:</b> 1800"
    ]
    assert message.reply_kwargs == [{"parse_mode": ParseMode.HTML}]


def test_handle_message_applies_expense_correction_and_updates_existing_entry():
    agent = _FakeAgent()
    agent.text_result = {
        "workflow_type": "expense_correction",
        "task_type": "expense",
        "record_id": "record-456",
        "expense_id": "expense-456",
        "analysis": {
            "description": "Bakery snack",
            "expense_total_amount_in_euros": 10.0,
            "category": "Bäcker",
        },
    }
    postgres_db = _FakePostgresDatabase()
    message = _FakeMessage()
    message.photo = []
    message.text = "Actually the amount was 10 euros and this belongs under bakery"
    update = SimpleNamespace(
        update_id=1019,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={},
    )
    remember_latest_tracking_result(
        context,
        {
            "task_type": "expense",
            "record_id": "record-456",
            "expense_id": "expense-456",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        },
    )
    remember_latest_expense_result(
        context,
        {
            "task_type": "expense",
            "record_id": "record-456",
            "expense_id": "expense-456",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        },
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["text"] == message.text
    assert agent.text_calls[0]["metadata"] == {
        "recent_history": [],
        "latest_expense_result": {
            "record_id": "record-456",
            "expense_id": "expense-456",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        },
        "latest_tracking_result": {
            "task_type": "expense",
            "record_id": "record-456",
            "meal_id": "",
            "expense_id": "expense-456",
            "dish_id": "",
            "analysis": {
                "description": "Groceries and toiletries",
                "expense_total_amount_in_euros": 43.2,
                "category": "Lebensmitteleinkäufe",
            },
        },
    }
    assert agent.updated_expense_records[0]["record_id"] == "record-456"
    assert postgres_db.updated_expense_calls[0]["expense_id"] == "expense-456"
    assert postgres_db.updated_expense_calls[0]["user_id"] == "user-123"
    latest_expense = get_latest_expense_result(context)
    assert latest_expense is not None
    assert latest_expense["analysis"]["expense_total_amount_in_euros"] == 10.0
    assert latest_expense["analysis"]["category"] == "Bäcker"
    assert message.replies == [
        "Updated your previous expense entry.\n"
        "Total: EUR 10.00\n"
        "Category: Bäcker\n"
        "Description: Bakery snack"
    ]
    assert message.reply_kwargs == [{}]


def test_handle_message_deletes_latest_nutrition_entry_from_text():
    agent = _FakeAgent(
        text_result={
            "workflow_type": "delete_latest_entry",
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "expense_id": "",
            "dish_id": "",
        }
    )
    postgres_db = _FakePostgresDatabase()
    message = _FakeMessage()
    message.photo = []
    message.text = "That was a mistake, please undo it"
    update = SimpleNamespace(
        update_id=1017,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    remember_latest_tracking_result(
        context,
        {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "analysis": agent.image_result["analysis"],
        },
    )
    remember_latest_nutrition_result(
        context,
        {
            "task_type": "nutrition",
            "record_id": "meal-123",
            "meal_id": "meal-123",
            "analysis": agent.image_result["analysis"],
        },
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["metadata"]["latest_tracking_result"]["record_id"] == "meal-123"
    assert agent.deleted_records == ["meal-123"]
    assert postgres_db.deleted_consumption_calls == [{"meal_id": "meal-123", "user_id": "user-123"}]
    assert postgres_db.daily_calories_calls == ["user-123"]
    assert get_latest_tracking_result(context) is None
    assert get_latest_nutrition_result(context) is None
    assert message.replies == ["Deleted your last nutrition entry. Today's total calories: 1800"]


def test_handle_message_deletes_latest_expense_entry_from_text():
    agent = _FakeAgent(
        text_result={
            "workflow_type": "delete_latest_entry",
            "task_type": "expense",
            "record_id": "record-456",
            "meal_id": "",
            "expense_id": "expense-456",
            "dish_id": "",
        }
    )
    postgres_db = _FakePostgresDatabase()
    message = _FakeMessage()
    message.photo = []
    message.text = "Please take that last one back"
    update = SimpleNamespace(
        update_id=1018,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    remember_latest_tracking_result(
        context,
        {
            "task_type": "expense",
            "record_id": "record-456",
            "expense_id": "expense-456",
            "analysis": {
                "description": "Groceries",
                "expense_total_amount_in_euros": 12.5,
                "category": "Lebensmitteleinkäufe",
            },
        },
    )
    remember_latest_expense_result(
        context,
        {
            "task_type": "expense",
            "record_id": "record-456",
            "expense_id": "expense-456",
            "analysis": {
                "description": "Groceries",
                "expense_total_amount_in_euros": 12.5,
                "category": "Lebensmitteleinkäufe",
            },
        },
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.deleted_records == ["record-456"]
    assert postgres_db.deleted_expense_calls == [{"expense_id": "expense-456", "user_id": "user-123"}]
    assert get_latest_tracking_result(context) is None
    assert get_latest_expense_result(context) is None
    assert message.replies == ["Deleted your last expense entry."]


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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["text"] == message.text
    assert agent.text_calls[0]["metadata"] == {"recent_history": []}
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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
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


def test_handle_message_tracks_nutrition_from_text():
    message = _FakeMessage()
    message.photo = []
    message.text = "2 croissants"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "nutrition_tracking",
            "task_type": "nutrition",
            "record_id": "meal-text-123",
            "analysis": {
                "ingredients": [
                    {"name": "croissant", "amount": "1 piece", "calories": 230.0},
                ],
                "category": "food",
                "calories": 460.0,
                "item_count": 2,
                "macros": {"carbs": 52.0, "protein": 10.0, "fat": 24.0},
                "tags": ["pastry"],
                "alcohol_units": 0.0,
            },
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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.image_calls == []
    assert agent.text_calls[0]["text"] == "2 croissants"
    assert postgres_db.consumption_calls[0]["meal_id"] == "meal-text-123"
    assert postgres_db.consumption_calls[0]["analysis"].calories == 460.0
    assert postgres_db.daily_calories_calls == ["user-123"]
    latest_result = get_latest_nutrition_result(context)
    assert latest_result is not None
    assert latest_result["record_id"] == "meal-text-123"
    assert latest_result["analysis"]["item_count"] == 2
    assert message.replies == [
        "<b>Ingredients</b>\n"
        "- Croissant : 1 piece (230.0 kcal)\n"
        "\n"
        "<b>Amount:</b> 2\n"
        "<b>Calories:</b> 2 * 230.0 = 460.0\n"
        "<b>Tags:</b> pastry\n"
        "\n"
        "<b>Today total calories:</b> 1800"
    ]
    assert message.reply_kwargs == [{"parse_mode": ParseMode.HTML}]


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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
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

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={}), agent))

    assert agent.image_calls == []
    assert agent.text_calls[0]["text"] == "hello there"
    assert agent.text_calls[0]["metadata"] == {"recent_history": []}
    assert agent.text_calls[0]["log_context"]["process_id"].startswith("telegram-")
    assert message.replies == [
        'Omg, I don\'t get it 🥺. '
        'Pleese give me more context about what you want 👉👈'
    ]


def test_handle_message_stores_new_vocabulary_entry():
    message = _FakeMessage()
    message.photo = []
    message.text = "bonjour"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "vocabulary",
            "assistant_reply": "Bonjour means hello. It is a common greeting in French.",
            "store_vocabulary": True,
            "french_word": "bonjour",
            "english_description": "hello; a common French greeting used when meeting someone.",
        }
    )
    update = SimpleNamespace(
        update_id=1016,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["metadata"] == {"recent_history": []}
    assert postgres_db.vocabulary_calls == [
        {
            "user_id": "user-123",
            "french_word": "bonjour",
            "english_description": "hello; a common French greeting used when meeting someone.",
        }
    ]
    assert message.replies == [
        "Bonjour means hello. It is a common greeting in French.\n\n"
        "Saved to your vocabulary. Reviews will arrive in the separate vocabulary bot."
    ]
    assert get_recent_history(context) == [
        {"role": "user", "text": "bonjour", "workflow": "vocabulary"},
        {
            "role": "assistant",
            "text": (
                "Bonjour means hello. It is a common greeting in French.\n\n"
                "Saved to your vocabulary. Reviews will arrive in the separate vocabulary bot."
            ),
            "workflow": "vocabulary",
        },
    ]


def test_handle_message_requires_vocab_bot_activation_before_storing(monkeypatch):
    message = _FakeMessage()
    message.photo = []
    message.text = "bonjour"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "vocabulary",
            "assistant_reply": "Bonjour means hello. It is a common greeting in French.",
            "store_vocabulary": True,
            "french_word": "bonjour",
            "english_description": "hello; a common French greeting used when meeting someone.",
        }
    )
    update = SimpleNamespace(
        update_id=1016,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()
    postgres_db.vocab_bot_activated = False
    monkeypatch.setattr(
        "src.bot.handlers.load_config",
        lambda: AppConfig(
            openai_api_key="test-key",
            vocab_bot_username="VocabTrainBot",
        ),
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.vocabulary_calls == []
    assert message.replies == [
        "Bonjour means hello. It is a common greeting in French.\n\n"
        "To save and review vocabulary, first activate the separate vocabulary bot: "
        "https://t.me/VocabTrainBot\n\n"
        "Open the link, press Start, and then send me the word again."
    ]


def test_handle_message_stores_recipe_collection_entry_from_text():
    message = _FakeMessage()
    message.photo = []
    message.text = "Add this to the recipes: lemon pasta with parmesan"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "recipe_collection",
            "text": "Add this to the recipes: lemon pasta with parmesan",
            "metadata": {"recent_history": []},
            "name": "Lemon pasta",
            "description": "Pasta with lemon, butter, and parmesan.",
            "carb_source": "noodles",
            "vegetarian": True,
            "meat": None,
            "frequency_rotation": "monthly",
        }
    )
    update = SimpleNamespace(
        update_id=1020,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(postgres_db.dish_calls) == 1
    assert postgres_db.dish_calls[0]["analysis"].name == "Lemon pasta"
    latest_result = get_latest_tracking_result(context)
    assert latest_result is not None
    assert latest_result["task_type"] == "recipe"
    assert latest_result["dish_id"] == "dish-123"
    assert message.replies == [
        "Recipe added to your collection.\n"
        "Name: Lemon pasta\n"
        "Description: Pasta with lemon, butter, and parmesan.\n"
        "Carb source: noodles\n"
        "Vegetarian: yes\n"
        "Frequency: monthly"
    ]


def test_handle_message_answers_vocabulary_follow_up_without_storing_again():
    message = _FakeMessage()
    message.photo = []
    message.text = "Can you give me an example sentence?"
    agent = _FakeAgent(
        text_result={
            "workflow_type": "vocabulary",
            "assistant_reply": 'Example: "Bonjour, comment vas-tu ?" means "Hello, how are you?"',
            "store_vocabulary": False,
            "french_word": None,
            "english_description": None,
        }
    )
    update = SimpleNamespace(
        update_id=1017,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={}),
        user_data={
            "_picflic_recent_messages": [
                {"role": "user", "text": "bonjour", "workflow": "vocabulary"},
                {
                    "role": "assistant",
                    "text": "Bonjour means hello. It is a common greeting in French.",
                    "workflow": "vocabulary",
                },
            ]
        },
    )
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert agent.text_calls[0]["metadata"] == {
        "recent_history": [
            {"role": "user", "text": "bonjour", "workflow": "vocabulary"},
            {
                "role": "assistant",
                "text": "Bonjour means hello. It is a common greeting in French.",
                "workflow": "vocabulary",
            },
        ]
    }
    assert postgres_db.vocabulary_calls == []
    assert message.replies == ['Example: "Bonjour, comment vas-tu ?" means "Hello, how are you?"']


def test_handle_message_does_not_treat_pending_review_as_main_bot_flow():
    message = _FakeMessage()
    message.photo = []
    message.text = "Bonjor"
    agent = _FakeAgent(text_result={"workflow_type": "echo"})
    update = SimpleNamespace(
        update_id=1018,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    postgres_db = _FakePostgresDatabase()
    postgres_db.pending_review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello; a common French greeting.",
        current_review_stage="day",
        next_review_at="2026-03-23T10:00:00",
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert len(agent.text_calls) == 1
    assert postgres_db.record_review_calls == []
    assert message.replies == [
        'Omg, I don\'t get it "big watery eyes smiley face". '
        'Pleese give me more context about what you want "fingers pointing at each other emoji"'
    ]


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

    asyncio.run(handle_message(update, SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={}), agent))

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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    result = {
        "task_type": "nutrition",
        "analysis": {
            "ingredients": [
                {"name": "beer", "amount": "500 ml", "calories": 151.7},
            ],
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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
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


def test_persist_result_creates_recipe_entry():
    postgres_db = _FakePostgresDatabase()
    update = SimpleNamespace(
        update_id=1021,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
    result = {
        "task_type": "recipe",
        "analysis": {
            "name": "Lemon pasta",
            "description": "Pasta with lemon, butter, and parmesan.",
            "carb_source": "noodles",
            "vegetarian": True,
            "meat": None,
            "frequency_rotation": "monthly",
        },
    }

    note = asyncio.run(persist_result(update, context, postgres_db, result))

    assert note == "Recipe added to your collection."
    assert len(postgres_db.dish_calls) == 1


def test_persist_result_requires_effective_user():
    with pytest.raises(ValueError, match="effective Telegram user"):
        asyncio.run(
            persist_result(
                SimpleNamespace(update_id=1004, effective_user=None),
                SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={}),
                _FakePostgresDatabase(),
                {
                    "task_type": "nutrition",
                    "analysis": {
                        "ingredients": [
                            {"name": "beer", "amount": "500 ml", "calories": 151.7},
                        ],
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


def test_format_result_response_formats_recipe():
    response = format_result_response(
        {
            "task_type": "recipe",
            "analysis": {
                "name": "Lemon pasta",
                "description": "Pasta with lemon, butter, and parmesan.",
                "carb_source": "noodles",
                "vegetarian": True,
                "meat": None,
                "frequency_rotation": "monthly",
            },
        },
        "Recipe added to your collection.",
    )

    assert response == (
        "Recipe added to your collection.\n"
        "Name: Lemon pasta\n"
        "Description: Pasta with lemon, butter, and parmesan.\n"
        "Carb source: noodles\n"
        "Vegetarian: yes\n"
        "Frequency: monthly"
    )


def test_format_result_response_formats_nutrition_with_ingredients_first():
    response = format_result_response(
        {
            "task_type": "nutrition",
            "analysis": {
                "ingredients": [
                    {"name": "beer", "amount": "500 ml", "calories": 151.7},
                ],
                "category": "drink",
                "calories": 151.7,
                "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
                "tags": ["alcoholic"],
                "alcohol_units": 1.5,
            },
        },
        "Today's total calories: 1800",
    )

    assert response == (
        "<b>Ingredients</b>\n"
        "- Beer : 500 ml (151.7 kcal)\n"
        "\n"
        "<b>Calories:</b> 151.7\n"
        "<b>Tags:</b> alcoholic\n"
        "\n"
        "<b>Today total calories:</b> 1800"
    )


def test_format_result_response_compacts_ingredient_name_and_amount():
    response = format_result_response(
        {
            "task_type": "nutrition",
            "analysis": {
                "ingredients": [
                    {
                        "name": "cherry tomatoes extra",
                        "amount": "about 25 g",
                        "calories": 5.0,
                    },
                ],
                "category": "food",
                "calories": 5.0,
                "macros": {"carbs": 1.0, "protein": 0.2, "fat": 0.0},
                "tags": ["vegetable"],
                "alcohol_units": 0.0,
            },
        },
    )

    assert response == (
        "<b>Ingredients</b>\n"
        "- Cherry tomatoes : ~25 g (5.0 kcal)\n"
        "\n"
        "<b>Calories:</b> 5.0\n"
        "<b>Tags:</b> vegetable"
    )


def test_format_result_response_includes_item_count_when_multiple_items():
    response = format_result_response(
        {
            "task_type": "nutrition",
            "analysis": {
                "ingredients": [
                    {"name": "croissant", "amount": "1 piece", "calories": 230.0},
                ],
                "category": "food",
                "calories": 690.0,
                "item_count": 3,
                "macros": {"carbs": 78.0, "protein": 15.0, "fat": 36.0},
                "tags": ["pastry"],
                "alcohol_units": 0.0,
            },
        },
    )

    assert response == (
        "<b>Ingredients</b>\n"
        "- Croissant : 1 piece (230.0 kcal)\n"
        "\n"
        "<b>Amount:</b> 3\n"
        "<b>Calories:</b> 3 * 230.0 = 690.0\n"
        "<b>Tags:</b> pastry"
    )


def test_format_vocabulary_response_appends_persistence_note():
    response = format_vocabulary_response(
        "Bonjour means hello. It is a common greeting in French.",
        "Saved to your vocabulary.",
    )

    assert response == (
        "Bonjour means hello. It is a common greeting in French.\n\nSaved to your vocabulary."
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


def test_remember_text_turn_keeps_recent_history_compact():
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})

    remember_text_turn(context, "bonjour", ["Bonjour means hello."], workflow_type="vocabulary")
    remember_text_turn(
        context,
        "Can you give me an example sentence?",
        ['Example: "Bonjour, comment vas-tu ?" means "Hello, how are you?"'],
        workflow_type="vocabulary",
    )

    assert get_recent_history(context) == [
        {
            "role": "user",
            "text": "bonjour",
            "workflow": "vocabulary",
        },
        {
            "role": "assistant",
            "text": "Bonjour means hello.",
            "workflow": "vocabulary",
        },
        {
            "role": "user",
            "text": "Can you give me an example sentence?",
            "workflow": "vocabulary",
        },
        {
            "role": "assistant",
            "text": 'Example: "Bonjour, comment vas-tu ?" means "Hello, how are you?"',
            "workflow": "vocabulary",
        },
    ]
