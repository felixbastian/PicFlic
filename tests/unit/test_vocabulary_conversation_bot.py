import asyncio
from types import SimpleNamespace

import pytest

from src.logging_context import clear_log_context
from src.vocab_conversation_bot import handle_message, start


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


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"


class _FakeConversationTrainer:
    def __init__(self, handled: bool = False) -> None:
        self.handled = handled
        self.calls: list[dict] = []

    async def handle_active_conversation_message(self, update, db) -> bool:
        self.calls.append({"update": update, "db": db})
        return self.handled


def test_start_activates_vocabulary_conversation_bot_for_user():
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
            "has_vocab_conversation_bot_activated": True,
        }
    ]
    assert message.replies == [
        "Vocabulary conversation training activated. I will start a short daily chat with you here."
    ]


def test_handle_message_routes_active_conversation_reply_to_trainer():
    message = _FakeMessage(text="Je pense a mon projet.")
    update = SimpleNamespace(
        update_id=5001,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    trainer = _FakeConversationTrainer(handled=True)
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, SimpleNamespace(), trainer, postgres_db))

    assert trainer.calls == [{"update": update, "db": postgres_db}]
    assert message.replies == []


def test_handle_message_replies_when_no_active_conversation_exists():
    message = _FakeMessage(text="hello?")
    update = SimpleNamespace(
        update_id=5002,
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
    )
    trainer = _FakeConversationTrainer(handled=False)
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, SimpleNamespace(), trainer, postgres_db))

    assert trainer.calls == [{"update": update, "db": postgres_db}]
    assert message.replies == [
        "No active conversation is waiting right now. I will start the next one here when it's due."
    ]
