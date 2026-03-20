from fastapi.testclient import TestClient

from src.api import app
from src.config import AppConfig


def test_health_reports_current_config(monkeypatch, tmp_path):
    config = AppConfig(
        openai_api_key="test-key",
        openai_model="gpt-5",
        database_path=tmp_path / "health.db",
        telegram_token="telegram-token",
        db_user="app_user",
        db_password="secret",
        db_name="app_db",
        db_host="127.0.0.1",
    )
    monkeypatch.setattr("src.api.load_config", lambda: config)

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database_path": str(tmp_path / "health.db"),
        "postgres_enabled": "true",
    }


def test_webhook_returns_500_when_bot_is_not_initialized():
    client = TestClient(app)

    response = client.post("/webhook/telegram", json={"update_id": 1})

    assert response.status_code == 500
    assert response.json()["detail"] == "Error processing update: 500: Bot not initialized"
