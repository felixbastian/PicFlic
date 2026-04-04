import asyncio
from types import SimpleNamespace

import pytest

from src.logging_context import clear_log_context
from src.models import DueVocabularyReview, ReferencedVocabularyReview, VocabularyReviewResult
from src.vocabulary_review import build_review_prompt_text
from src.vocab_bot import dispatch_due_vocabulary_reviews, handle_message, start


@pytest.fixture(autouse=True)
def _clear_logging_context_between_tests():
    clear_log_context()
    yield
    clear_log_context()


class _FakeMessage:
    def __init__(self, text: str | None = None, reply_to_message=None, quote=None) -> None:
        self.text = text
        self.reply_to_message = reply_to_message
        self.quote = quote
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
        self.stale_reviews: list[DueVocabularyReview] = []
        self.mark_prompted_calls: list[str] = []
        self.prompt_reference: ReferencedVocabularyReview | None = None
        self.word_reference: ReferencedVocabularyReview | None = None
        self.prompt_lookup_calls: list[dict] = []
        self.word_lookup_calls: list[dict] = []
        self.record_review_calls: list[dict] = []

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"

    async def list_due_vocabulary_reviews(self, limit: int = 100) -> list[DueVocabularyReview]:
        return self.due_reviews[:limit]

    async def list_stale_vocabulary_review_reminders(self, limit: int = 100, resend_after=None) -> list[DueVocabularyReview]:
        return self.stale_reviews[:limit]

    async def mark_vocabulary_review_prompted(self, vocabulary_id: str) -> None:
        self.mark_prompted_calls.append(vocabulary_id)

    async def get_next_due_vocabulary_review_for_user(self, user_id: str) -> DueVocabularyReview | None:
        for review in self.due_reviews:
            if review.user_id == user_id:
                return review
        return None

    async def get_recent_prompted_vocabulary_review_by_prompt(
        self,
        telegram_user_id: int,
        prompt_text: str,
        limit: int = 25,
    ) -> ReferencedVocabularyReview | None:
        self.prompt_lookup_calls.append(
            {
                "telegram_user_id": telegram_user_id,
                "prompt_text": prompt_text,
                "limit": limit,
            }
        )
        return self.prompt_reference

    async def get_recent_prompted_vocabulary_review_by_french_word(
        self,
        telegram_user_id: int,
        french_word: str,
        limit: int = 25,
    ) -> ReferencedVocabularyReview | None:
        self.word_lookup_calls.append(
            {
                "telegram_user_id": telegram_user_id,
                "french_word": french_word,
                "limit": limit,
            }
        )
        return self.word_reference

    async def record_vocabulary_review_result(self, vocabulary_id: str, correct: bool = False, shelved: bool = False):
        self.record_review_calls.append(
            {
                "vocabulary_id": vocabulary_id,
                "correct": correct,
                "shelved": shelved,
            }
        )
        return VocabularyReviewResult(
            vocabulary_id=vocabulary_id,
            user_id="user-123",
            french_word="aller",
            correct=correct,
            shelved=shelved,
            finished=False,
            current_review_stage=None,
            next_review_at=None,
        )


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
            "dispatch_next_due_review": True,
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
                "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
                "Reply 'shelf' if you want me to stop reviewing this word."
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
            "dispatch_next_due_review": False,
        }
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert message.replies == [
        "No vocabulary review is waiting right now. Use the main bot to save new words first."
    ]
    assert application.bot.sent_messages == []
    assert postgres_db.mark_prompted_calls == []


def test_handle_message_does_not_dispatch_next_due_review_while_sentence_practice_is_pending():
    message = _FakeMessage(text="bonjour")
    update = SimpleNamespace(
        update_id=4006,
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
            "response": (
                'Correct. The French word is "bonjour". I will ask you again in 3 days.\n\n'
                'Write one short French sentence using "bonjour". Reply \'p\' or \'pass\' to skip this part.'
            ),
            "review_result": VocabularyReviewResult(
                vocabulary_id="vocab-1",
                user_id="user-123",
                french_word="bonjour",
                correct=True,
                shelved=False,
                finished=False,
                current_review_stage="three_days",
                next_review_at=None,
                awaiting_sentence=True,
            ),
            "dispatch_next_due_review": False,
        }
    )

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert message.replies == [
        'Correct. The French word is "bonjour". I will ask you again in 3 days.\n\n'
        'Write one short French sentence using "bonjour". Reply \'p\' or \'pass\' to skip this part.'
    ]
    assert application.bot.sent_messages == []


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
                "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
                "Reply 'shelf' if you want me to stop reviewing this word."
            ),
        }
    ]
    assert postgres_db.mark_prompted_calls == ["vocab-1"]


def test_dispatch_due_vocabulary_reviews_resends_stale_pending_before_new_due_reviews():
    application = _FakeApplication()
    postgres_db = _FakePostgresDatabase()
    postgres_db.stale_reviews = [
        DueVocabularyReview(
            vocabulary_id="vocab-stale",
            user_id="user-1",
            telegram_user_id=42,
            french_word="aller",
            english_description="to go",
            current_review_stage="day",
            next_review_at="2026-03-23T10:00:00",
        )
    ]
    postgres_db.due_reviews = [
        DueVocabularyReview(
            vocabulary_id="vocab-due",
            user_id="user-2",
            telegram_user_id=77,
            french_word="bonjour",
            english_description="hello",
            current_review_stage="day",
            next_review_at="2026-03-23T10:05:00",
        )
    ]

    sent_count = asyncio.run(dispatch_due_vocabulary_reviews(application, postgres_db, limit=2))

    assert sent_count == 2
    assert application.bot.sent_messages == [
        {
            "chat_id": 42,
            "text": (
                "Vocabulary review:\n"
                "What is the French word for:\nto go\n\n"
                "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
                "Reply 'shelf' if you want me to stop reviewing this word."
            ),
        },
        {
            "chat_id": 77,
            "text": (
                "Vocabulary review:\n"
                "What is the French word for:\nhello\n\n"
                "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
                "Reply 'shelf' if you want me to stop reviewing this word."
            ),
        },
    ]
    assert postgres_db.mark_prompted_calls == ["vocab-stale", "vocab-due"]


def test_dispatch_due_vocabulary_reviews_resends_pending_sentence_prompt():
    application = _FakeApplication()
    postgres_db = _FakePostgresDatabase()
    postgres_db.stale_reviews = [
        DueVocabularyReview(
            vocabulary_id="vocab-sentence",
            user_id="user-123",
            telegram_user_id=42,
            french_word="bonjour",
            english_description="hello",
            current_review_stage=None,
            next_review_at=None,
            awaiting_sentence=True,
            sentence_attempts=1,
        )
    ]

    sent_count = asyncio.run(dispatch_due_vocabulary_reviews(application, postgres_db))

    assert sent_count == 1
    assert application.bot.sent_messages == [
        {
            "chat_id": 42,
            "text": 'Try one more short French sentence using "bonjour". Reply \'p\' or \'pass\' to skip this part.',
        }
    ]
    assert postgres_db.mark_prompted_calls == ["vocab-sentence"]


def test_handle_message_shelves_quoted_review_prompt_after_answer():
    quoted_prompt = build_review_prompt_text("to go")
    message = _FakeMessage(
        text="shelf",
        reply_to_message=SimpleNamespace(text=quoted_prompt),
    )
    update = SimpleNamespace(
        update_id=4003,
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
    postgres_db.prompt_reference = ReferencedVocabularyReview(
        vocabulary_id="vocab-9",
        user_id="user-123",
        telegram_user_id=42,
        french_word="aller",
        english_description="to go",
    )
    agent = _FakeVocabularyAgent()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.prompt_lookup_calls == [
        {
            "telegram_user_id": 42,
            "prompt_text": quoted_prompt,
            "limit": 25,
        }
    ]
    assert postgres_db.record_review_calls == [
        {
            "vocabulary_id": "vocab-9",
            "correct": False,
            "shelved": True,
        }
    ]
    assert agent.calls == []
    assert message.replies == ['Okay, I shelved "aller" for you.']


def test_handle_message_shelves_quoted_wrong_answer_feedback_by_french_word():
    quoted_feedback = 'Not quite. The correct word is "accru, accroître". I will ask you again tomorrow.'
    message = _FakeMessage(
        text="shelf",
        reply_to_message=SimpleNamespace(text=quoted_feedback),
    )
    update = SimpleNamespace(
        update_id=4004,
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
    postgres_db.word_reference = ReferencedVocabularyReview(
        vocabulary_id="vocab-10",
        user_id="user-123",
        telegram_user_id=42,
        french_word="accru, accroître",
        english_description="to increase",
    )
    agent = _FakeVocabularyAgent()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.prompt_lookup_calls == [
        {
            "telegram_user_id": 42,
            "prompt_text": quoted_feedback,
            "limit": 25,
        }
    ]
    assert postgres_db.word_lookup_calls == [
        {
            "telegram_user_id": 42,
            "french_word": "accru, accroître",
            "limit": 25,
        }
    ]
    assert postgres_db.record_review_calls == [
        {
            "vocabulary_id": "vocab-10",
            "correct": False,
            "shelved": True,
        }
    ]
    assert agent.calls == []
    assert message.replies == ['Okay, I shelved "accru, accroître" for you.']


def test_handle_message_shelves_quoted_correct_answer_feedback_by_french_word():
    quoted_feedback = 'Correct. The French word is "aller". I will ask you again in 3 days.'
    message = _FakeMessage(
        text="shelf",
        reply_to_message=SimpleNamespace(text=quoted_feedback),
    )
    update = SimpleNamespace(
        update_id=4005,
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
    postgres_db.word_reference = ReferencedVocabularyReview(
        vocabulary_id="vocab-11",
        user_id="user-123",
        telegram_user_id=42,
        french_word="aller",
        english_description="to go",
    )
    agent = _FakeVocabularyAgent()

    asyncio.run(handle_message(update, context, agent, postgres_db))

    assert postgres_db.word_lookup_calls == [
        {
            "telegram_user_id": 42,
            "french_word": "aller",
            "limit": 25,
        }
    ]
    assert postgres_db.record_review_calls == [
        {
            "vocabulary_id": "vocab-11",
            "correct": False,
            "shelved": True,
        }
    ]
    assert agent.calls == []
    assert message.replies == ['Okay, I shelved "aller" for you.']
