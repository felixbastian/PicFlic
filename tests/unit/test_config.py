from src.config import load_config


def test_load_config_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PICTOAGENT_OPENAI_MODEL", raising=False)
    load_config.cache_clear()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=test-key\n"
        "PICTOAGENT_OPENAI_MODEL=test-model\n"
        "PICTOAGENT_DATABASE_PATH=./data/test.db\n"
    )

    config = load_config(env_file)

    assert config.openai_api_key == "test-key"
    assert config.openai_model == "test-model"
    assert config.database_path.name == "test.db"
    assert config.database_path.parent.name == "data"


def test_load_config_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("PICTOAGENT_OPENAI_MODEL", "env-model")
    monkeypatch.setenv("PICTOAGENT_DATABASE_PATH", "/tmp/env.db")
    load_config.cache_clear()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=file-key\n"
        "PICTOAGENT_OPENAI_MODEL=file-model\n"
        "PICTOAGENT_DATABASE_PATH=./data/file.db\n"
    )

    config = load_config(env_file)

    assert config.openai_api_key == "env-key"
    assert config.openai_model == "env-model"
    assert str(config.database_path) == "/tmp/env.db"
