import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.bot import (
    dispatch_due_vocabulary_reviews,
    format_multirow_query_response,
    format_query_response,
    format_result_response,
    format_vocabulary_response,
    get_recent_history,
    handle_message,
    persist_result,
    remember_text_turn,
    resolve_user_id,
    start,
)
from src.logging_context import clear_log_context, get_log_context
from src.models import DueVocabularyReview, ExpenseAnalysis, NutritionAnalysis, RecipeAnalysis, VocabularyReviewResult


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
        self.dish_calls: list[dict] = []
        self.vocabulary_calls: list[dict] = []
        self.daily_calories_calls: list[str] = []
        self.query_calls: list[dict] = []
        self.mark_prompted_calls: list[str] = []
        self.record_review_calls: list[dict] = []
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

    async def store_consumption(self, user_id: str, analysis: NutritionAnalysis) -> str:
        self.consumption_calls.append({"user_id": user_id, "analysis": analysis})
        return "meal-123"

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
        "Hi! Send me a photo of your food or a receipt, ask about your tracked expenses and nutrition, send me a French word to practice vocabulary, or tell me to save a recipe to your collection."
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
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})
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
    assert message.replies == ["hello there"]


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
        "Bonjour means hello. It is a common greeting in French.\n\nSaved to your vocabulary."
    ]
    assert get_recent_history(context) == [
        {"role": "user", "text": "bonjour", "workflow": "vocabulary"},
        {
            "role": "assistant",
            "text": "Bonjour means hello. It is a common greeting in French.\n\nSaved to your vocabulary.",
            "workflow": "vocabulary",
        },
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


def test_handle_message_treats_pending_review_answer_as_review_flow():
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
    context = SimpleNamespace(application=_FakeApplication(), user_data={})
    context.application.bot_data = {}
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

    assert agent.text_calls == []
    assert postgres_db.record_review_calls == [
        {"vocabulary_id": "vocab-1", "correct": True, "shelved": False}
    ]
    assert message.replies == ['Correct. The French word is "bonjour". I will ask you again in 3 days.']
    assert context.application.bot.sent_messages == []


def test_handle_message_shelves_pending_review():
    message = _FakeMessage()
    message.photo = []
    message.text = "please shelf this one"
    agent = _FakeAgent(text_result={"workflow_type": "echo"})
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
    context = SimpleNamespace(application=_FakeApplication(), user_data={})
    context.application.bot_data = {}
    postgres_db = _FakePostgresDatabase()
    postgres_db.pending_review = DueVocabularyReview(
        vocabulary_id="vocab-2",
        user_id="user-123",
        telegram_user_id=42,
        french_word="fromage",
        english_description="cheese",
        current_review_stage="week",
        next_review_at="2026-03-29T10:00:00",
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.record_review_calls == [
        {"vocabulary_id": "vocab-2", "correct": False, "shelved": True}
    ]
    assert message.replies == ['Okay, I shelved "fromage". I will stop asking you this word.']
    assert context.application.bot.sent_messages == []


def test_handle_message_sends_next_due_review_immediately_after_answer():
    message = _FakeMessage()
    message.photo = []
    message.text = "Bonjor"
    agent = _FakeAgent(text_result={"workflow_type": "echo"})
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
    context = SimpleNamespace(application=_FakeApplication(), user_data={})
    context.application.bot_data = {}
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
    postgres_db.due_reviews = [
        DueVocabularyReview(
            vocabulary_id="vocab-2",
            user_id="user-123",
            telegram_user_id=42,
            french_word="fromage",
            english_description="cheese",
            current_review_stage="week",
            next_review_at="2026-03-23T10:05:00",
        )
    ]

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert message.replies == ['Correct. The French word is "bonjour". I will ask you again in 3 days.']
    assert context.application.bot.sent_messages == [
        {
            "chat_id": 42,
            "text": (
                "Vocabulary review:\n"
                "What is the French word for:\ncheese\n\n"
                "Reply with the French word. Reply 'shelf' if you want me to stop reviewing this word."
            ),
        }
    ]
    assert postgres_db.mark_prompted_calls == ["vocab-2"]


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


def test_dispatch_due_vocabulary_reviews_sends_one_prompt_per_due_review():
    application = _FakeApplication()
    postgres_db = _FakePostgresDatabase()
    postgres_db.due_reviews = [
        DueVocabularyReview(
            vocabulary_id="vocab-1",
            user_id="user-123",
            telegram_user_id=42,
            french_word="bonjour",
            english_description="hello; a common French greeting.",
            current_review_stage="day",
            next_review_at="2026-03-23T10:00:00",
        )
    ]

    sent_count = asyncio.run(dispatch_due_vocabulary_reviews(application, postgres_db))

    assert sent_count == 1
    assert application.bot.sent_messages == [
        {
            "chat_id": 42,
            "text": (
                "Vocabulary review:\n"
                "What is the French word for:\nhello; a common French greeting.\n\n"
                "Reply with the French word. Reply 'shelf' if you want me to stop reviewing this word."
            ),
        }
    ]
    assert postgres_db.mark_prompted_calls == ["vocab-1"]
