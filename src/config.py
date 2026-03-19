"""Configuration loading for PictoAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "pictoagent.db"


@dataclass(frozen=True)
class AppConfig:
    openai_api_key: str | None
    openai_model: str = "gpt-5"
    database_path: Path = DEFAULT_DATABASE_PATH
    telegram_token: str | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    db_host: str | None = None
    db_port: int = 5432
    instance_connection_name: str | None = None

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.db_user and self.db_name and (self.db_host or self.instance_connection_name))


@lru_cache(maxsize=1)
def load_config(env_file: str | Path = DEFAULT_ENV_FILE) -> AppConfig:
    env_values = _read_env_file(Path(env_file))

    return AppConfig(
        openai_api_key=os.getenv("OPENAI_API_KEY") or env_values.get("OPENAI_API_KEY"),
        openai_model=os.getenv("PICTOAGENT_OPENAI_MODEL")
        or env_values.get("PICTOAGENT_OPENAI_MODEL")
        or "gpt-5",
        database_path=_resolve_database_path(
            os.getenv("PICTOAGENT_DATABASE_PATH")
            or env_values.get("PICTOAGENT_DATABASE_PATH")
        ),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or env_values.get("TELEGRAM_BOT_TOKEN"),
        db_user=os.getenv("DB_USER") or env_values.get("DB_USER"),
        db_password=os.getenv("DB_PASSWORD") or env_values.get("DB_PASSWORD"),
        db_name=os.getenv("DB_NAME") or env_values.get("DB_NAME"),
        db_host=os.getenv("DB_HOST") or env_values.get("DB_HOST"),
        db_port=_resolve_port(os.getenv("DB_PORT") or env_values.get("DB_PORT")),
        instance_connection_name=os.getenv("INSTANCE_CONNECTION_NAME")
        or env_values.get("INSTANCE_CONNECTION_NAME"),
    )


def _read_env_file(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')

    return values


def _resolve_database_path(raw_path: str | None) -> Path:
    if not raw_path:
        return DEFAULT_DATABASE_PATH

    database_path = Path(raw_path).expanduser()
    if database_path.is_absolute():
        return database_path

    return PROJECT_ROOT / database_path


def _resolve_port(raw_port: str | None) -> int:
    if not raw_port:
        return 5432

    return int(raw_port)
