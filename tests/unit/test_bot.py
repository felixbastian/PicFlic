import asyncio
from types import SimpleNamespace

import pytest

from src.bot import handle_message, persist_consumption, start
from src.models import ImageAnalysis


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
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeAgent:
    def process_image(self, image_path: str) -> dict:
        return {
            "record_id": "meal-123",
            "analysis": {
                "category": "drink",
                "calories": 151.7,
                "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
                "tags": ["alcoholic"],
                "alcohol_units": 1.5,
            },
        }


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.user_calls: list[dict] = []
        self.consumption_calls: list[dict] = []
        self.daily_calories_calls: list[str] = []

    async def get_or_create_user(self, **kwargs) -> str:
        self.user_calls.append(kwargs)
        return "user-123"

    async def store_consumption(self, user_id: str, analysis: ImageAnalysis) -> str:
        self.consumption_calls.append({"user_id": user_id, "analysis": analysis})
        return "meal-123"

    async def get_daily_calories(self, user_id: str) -> int:
        self.daily_calories_calls.append(user_id)
        return 1800


def test_start_replies_with_welcome_message():
    message = _FakeMessage()
    update = SimpleNamespace(message=message)

    asyncio.run(start(update, SimpleNamespace()))

    assert message.replies == ["Hi! Send me a photo of your food and I'll analyze it!"]


def test_handle_message_stores_fact_consumption():
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

    asyncio.run(handle_message(update, SimpleNamespace(), _FakeAgent(), postgres_db))

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


def test_handle_message_reuses_webhook_resolved_user_id():
    message = _FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
        message=message,
        _picflic_user_id="user-from-webhook",
    )
    postgres_db = _FakePostgresDatabase()

    asyncio.run(handle_message(update, SimpleNamespace(), _FakeAgent(), postgres_db))

    assert postgres_db.user_calls == []
    assert len(postgres_db.consumption_calls) == 1
    assert postgres_db.consumption_calls[0]["user_id"] == "user-from-webhook"
    assert postgres_db.daily_calories_calls == ["user-from-webhook"]


def test_persist_consumption_creates_user_when_needed():
    postgres_db = _FakePostgresDatabase()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(
            id=42,
            username="felix",
            first_name="Felix",
            last_name="Hans",
        ),
    )
    analysis = {
        "category": "drink",
        "calories": 151.7,
        "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
        "tags": ["alcoholic"],
        "alcohol_units": 1.5,
    }

    meal_id, user_id = asyncio.run(persist_consumption(update, postgres_db, analysis))

    assert meal_id == "meal-123"
    assert user_id == "user-123"
    assert len(postgres_db.user_calls) == 1
    assert len(postgres_db.consumption_calls) == 1


def test_persist_consumption_requires_effective_user():
    with pytest.raises(ValueError, match="effective Telegram user"):
        asyncio.run(
            persist_consumption(
                SimpleNamespace(effective_user=None),
                _FakePostgresDatabase(),
                {
                    "category": "drink",
                    "calories": 151.7,
                    "macros": {"carbs": 12.0, "protein": 1.0, "fat": 0.0},
                    "tags": ["alcoholic"],
                    "alcohol_units": 1.5,
                },
            )
        )
