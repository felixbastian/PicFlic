import asyncio
from types import SimpleNamespace

import pytest

from src.logging_context import clear_log_context
from src.models import DueVocabularyReview, VocabularyReviewResult
from src.vocab_bot import dispatch_due_vocabulary_reviews, handle_message, start


@pytest.fixture(autouse=True)
def _clear_logging_context_between_tests():
    clear_log_context()
    yield
    clear_log_context()


class _FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[str] = []
        self.reply_kwargs: list[dict] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)


class _FakeTelegramBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text})


class _FakeApplication:
    def __init__(self) -> None:
        self.bot = _FakeTelegramBot()
        self.bot_data = {}


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []
        self.due_reviews: list[DueVocabularyReview] = []
        self.mark_prompted_calls: list[str] = []

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"

    async def list_due_vocabulary_reviews(self, limit: int = 100) -> list[DueVocabularyReview]:
        return self.due_reviews[:limit]

    async def mark_vocabulary_review_prompted(self, vocabulary_id: str) -> None:
        self.mark_prompted_calls.append(vocabulary_id)

    async def get_next_due_vocabulary_review_for_user(self, user_id: str) -> DueVocabularyReview | None:
        for review in self.due_reviews:
            if review.user_id == user_id:
                return review
        return None


class _FakeVocabularyAgent:
    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or {
            "response": 'Correct. The French word is "bonjour". I will ask you again in 3 days.',
            "review_result": VocabularyReviewResult(
                vocabulary_id="vocab-1",
                user_id="user-123",
                french_word="bonjour",
                correct=True,
                shelved=False,
                finished=False,
                current_review_stage="three_days",
                next_review_at=None,
            ),
        }

    async def process_review_answer(self, telegram_user_id: int, answer_text: str, db) -> dict:
        self.calls.append(
            {
                "telegram_user_id": telegram_user_id,
                "answer_text": answer_text,
                "db": db,
            }
        )
        return self.result


def test_start_activates_vocabulary_bot_for_user():
    message = _FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    postgres_db = _FakePostgresDatabase()

    asyncio.run(start(update, SimpleNamespace(), postgres_db))

    assert postgres_db.user_calls == [
        {
            "telegram_user_id": 42,
            "username": "felix",
            "first_name": "Felix",
            "last_name": "Hans",
            "has_vocab_bot_activated": True,
        }
    ]
    assert message.replies == ["Vocabulary training activated. I will send your review prompts here."]


def test_handle_message_processes_review_in_separate_bot_and_dispatches_next_due_prompt():
    message = _FakeMessage(text="Bonjor")
    update = SimpleNamespace(
        update_id=4001,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    application = _FakeApplication()
    context = SimpleNamespace(application=application)
    postgres_db = _FakePostgresDatabase()
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
    agent = _FakeVocabularyAgent()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.user_calls == [
        {
            "telegram_user_id": 42,
            "username": "felix",
            "first_name": "Felix",
            "last_name": "Hans",
            "has_vocab_bot_activated": True,
        }
    ]
    assert agent.calls == [
        {
            "telegram_user_id": 42,
            "answer_text": "Bonjor",
            "db": postgres_db,
        }
    ]
    assert message.replies == ['Correct. The French word is "bonjour". I will ask you again in 3 days.']
    assert application.bot.sent_messages == [
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


def test_handle_message_replies_when_no_review_is_pending():
    message = _FakeMessage(text="hello?")
    update = SimpleNamespace(
        update_id=4002,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    application = _FakeApplication()
    context = SimpleNamespace(application=application)
    postgres_db = _FakePostgresDatabase()
    agent = _FakeVocabularyAgent(
        result={
            "response": "No vocabulary review is waiting right now. Use the main bot to save new words first.",
            "review_result": None,
        }
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert message.replies == [
        "No vocabulary review is waiting right now. Use the main bot to save new words first."
    ]
    assert application.bot.sent_messages == []
    assert postgres_db.mark_prompted_calls == []


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
