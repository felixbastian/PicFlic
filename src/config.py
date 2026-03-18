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
