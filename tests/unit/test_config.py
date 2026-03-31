from src.config import load_config


def test_load_config_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PICTOAGENT_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.delenv("INSTANCE_CONNECTION_NAME", raising=False)
    monkeypatch.delenv("PICTOAGENT_REVIEW_JOB_SECRET", raising=False)
    monkeypatch.delenv("VOCAB_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("VOCAB_TELEGRAM_BOT_USERNAME", raising=False)
    load_config.cache_clear()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=test-key\n"
        "PICTOAGENT_OPENAI_MODEL=test-model\n"
        "PICTOAGENT_DATABASE_PATH=./data/test.db\n"
        "VOCAB_TELEGRAM_BOT_TOKEN=vocab-token\n"
        "VOCAB_TELEGRAM_BOT_USERNAME=VocabTrainBot\n"
        "DB_USER=app_user\n"
        "DB_PASSWORD=secret\n"
        "DB_NAME=app_db\n"
        "INSTANCE_CONNECTION_NAME=project:region:instance\n"
        "PICTOAGENT_REVIEW_JOB_SECRET=job-secret\n"
    )

    config = load_config(env_file)

    assert config.openai_api_key == "test-key"
    assert config.openai_model == "test-model"
    assert config.database_path.name == "test.db"
    assert config.database_path.parent.name == "data"
    assert config.vocab_telegram_token == "vocab-token"
    assert config.vocab_bot_username == "VocabTrainBot"
    assert config.vocab_bot_link == "https://t.me/VocabTrainBot"
    assert config.db_user == "app_user"
    assert config.db_password == "secret"
    assert config.db_name == "app_db"
    assert config.instance_connection_name == "project:region:instance"
    assert config.review_job_secret == "job-secret"
    assert config.postgres_enabled is True


def test_load_config_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("PICTOAGENT_OPENAI_MODEL", "env-model")
    monkeypatch.setenv("PICTOAGENT_DATABASE_PATH", "/tmp/env.db")
    monkeypatch.setenv("DB_USER", "env-user")
    monkeypatch.setenv("DB_PASSWORD", "env-password")
    monkeypatch.setenv("DB_NAME", "env-db")
    monkeypatch.setenv("INSTANCE_CONNECTION_NAME", "env-project:env-region:env-instance")
    monkeypatch.setenv("PICTOAGENT_REVIEW_JOB_SECRET", "env-job-secret")
    monkeypatch.setenv("VOCAB_TELEGRAM_BOT_TOKEN", "env-vocab-token")
    monkeypatch.setenv("VOCAB_TELEGRAM_BOT_USERNAME", "EnvVocabBot")
    load_config.cache_clear()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=file-key\n"
        "PICTOAGENT_OPENAI_MODEL=file-model\n"
        "PICTOAGENT_DATABASE_PATH=./data/file.db\n"
        "VOCAB_TELEGRAM_BOT_TOKEN=file-vocab-token\n"
        "VOCAB_TELEGRAM_BOT_USERNAME=FileVocabBot\n"
        "DB_USER=file-user\n"
        "DB_PASSWORD=file-password\n"
        "DB_NAME=file-db\n"
        "INSTANCE_CONNECTION_NAME=file-project:file-region:file-instance\n"
        "PICTOAGENT_REVIEW_JOB_SECRET=file-job-secret\n"
    )

    config = load_config(env_file)

    assert config.openai_api_key == "env-key"
    assert config.openai_model == "env-model"
    assert str(config.database_path) == "/tmp/env.db"
    assert config.vocab_telegram_token == "env-vocab-token"
    assert config.vocab_bot_username == "EnvVocabBot"
    assert config.vocab_bot_link == "https://t.me/EnvVocabBot"
    assert config.db_user == "env-user"
    assert config.db_password == "env-password"
    assert config.db_name == "env-db"
    assert config.instance_connection_name == "env-project:env-region:env-instance"
    assert config.review_job_secret == "env-job-secret"
