from fastapi.testclient import TestClient

import src.api as api
from src.config import AppConfig


def test_health_reports_current_config(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        vocab_conversation_telegram_token="conversation-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)

    client = TestClient(api.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database_path": str(tmp_path / "health.db"),
        "postgres_enabled": "true",
        "vocab_bot_enabled": "false",
        "vocab_conversation_bot_enabled": "true",
    }


def test_webhook_returns_500_when_bot_is_not_initialized():
    client = TestClient(api.app)

    response = client.post("/webhook/telegram", json={"update_id": 1})

    assert response.status_code == 500
    assert response.json()["detail"] == "Error processing update: 500: Bot not initialized"


def test_vocabulary_webhook_returns_500_when_bot_is_not_initialized():
    client = TestClient(api.app)

    response = client.post("/webhook/telegram/vocabulary", json={"update_id": 1})

    assert response.status_code == 500
    assert response.json()["detail"] == "Error processing update: 500: Bot not initialized"


def test_vocabulary_conversation_webhook_returns_500_when_bot_is_not_initialized():
    client = TestClient(api.app)

    response = client.post("/webhook/telegram/vocabulary-conversation", json={"update_id": 1})

    assert response.status_code == 500
    assert response.json()["detail"] == "Error processing update: 500: Bot not initialized"


def test_vocabulary_review_job_requires_secret(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
        review_job_secret="top-secret",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)

    client = TestClient(api.app)
    response = client.post("/jobs/vocabulary-reviews/run", headers={"X-Job-Secret": "wrong"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_vocabulary_review_job_dispatches_due_prompts(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
        review_job_secret="top-secret",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)
    async def _dispatch(application, db):
        return 2

    monkeypatch.setattr("src.api._vocab_bot_application", object())
    monkeypatch.setattr("src.api._db", object())
    monkeypatch.setattr("src.api.dispatch_due_vocabulary_reviews", _dispatch)

    client = TestClient(api.app)
    response = client.post("/jobs/vocabulary-reviews/run", headers={"X-Job-Secret": "top-secret"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "sent_count": 2}


def test_vocabulary_conversation_job_requires_secret(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
        review_job_secret="top-secret",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)

    client = TestClient(api.app)
    response = client.post("/jobs/vocabulary-conversations/run", headers={"X-Job-Secret": "wrong"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_vocabulary_conversation_job_starts_daily_conversations(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
        review_job_secret="top-secret",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)

    class _FakeConversationTrainer:
        async def dispatch_daily_conversations(self, application, db):
            return 3

    monkeypatch.setattr("src.api._vocab_conversation_bot_application", object())
    monkeypatch.setattr("src.api._db", object())
    monkeypatch.setattr("src.api._vocab_conversation_trainer", _FakeConversationTrainer())

    client = TestClient(api.app)
    response = client.post("/jobs/vocabulary-conversations/run", headers={"X-Job-Secret": "top-secret"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "started_count": 3}
